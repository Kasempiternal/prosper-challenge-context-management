import json
import math
import time

import httpx
import numpy as np
import pytest
import tiktoken

from agent_builder import validate_agent
from agent_builder.schema import ResolverConfig
from agent_tools.context import load_index, make_context, resolve_catalog_path
from scheduling.decision import DECLINE, Gate
from scheduling.embed_chooser import EmbedChooser, EmbedClient, FastEmbedder
from scheduling.jev import JevClient, JevTypeDisambiguator
from scheduling.openai_chooser import (OpenAIChoiceClient, SpendCapExceeded, distribution, option_keys,
                                       request_body)
from scheduling.resolver import NoDisambiguator


@pytest.fixture(scope="module")
def sf():
    return load_index(resolve_catalog_path("data/catalog.json"))


# ---- logprobs -> distribution -------------------------------------------------------------

def test_key_spellings_add_up_and_mass_off_the_options_stays_uncertain():
    keys = {"appt_a": "1", "appt_b": "2", "appt_c": "3"}
    top = [["1", math.log(0.5)], [" 1", math.log(0.2)], ["2", math.log(0.1)], ["0", math.log(0.15)],
           ["Hello", math.log(0.05)]]
    ans = distribution(top, keys)
    assert ans.probabilities == pytest.approx({"appt_a": 0.7, "appt_b": 0.1})
    assert ans.other_mass == pytest.approx(0.2)
    # 0.7 alone would be "act" if the 0.2 were renormalized onto the options (0.875); it is not.
    assert Gate().decide(ans.probabilities).act is None


def test_a_sure_answer_acts():
    ans = distribution([["2", -0.0001], ["1", -12.0]], {"x": "1", "y": "2"})
    assert Gate().decide(ans.probabilities).act == "y"


def test_option_keys_are_single_o200k_tokens(sf):
    criteria = {f"opt{i:03d}": "text" for i in range(255)}
    keys = option_keys(criteria)
    enc = tiktoken.get_encoding("o200k_base")
    assert list(keys.values())[:3] == ["1", "2", "3"] and keys["opt254"] == "255"
    assert all(len(enc.encode(k)) == 1 for k in keys.values())


def test_request_shows_every_option_behind_its_key_and_asks_for_one_token():
    body = request_body("gpt-4o-mini", "said 'ears ringing'", "Which type?", {"a": "ENT visit", "b": "Hearing test"},
                        {"a": "1", "b": "2"})
    assert (body["max_completion_tokens"], body["logprobs"], body["top_logprobs"], body["temperature"]) == (1, True, 20, 0)
    user = body["messages"][1]["content"]
    assert "1: ENT visit\n2: Hearing test\n0: none of these" in user


# ---- OpenAI client: network, cache, timeout, spend cap -------------------------------------

def completion(top: list[tuple[str, float]], prompt_tokens: int = 700) -> dict:
    entries = [{"token": t, "logprob": lp, "bytes": list(t.encode())} for t, lp in top]
    return {"id": "c", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini",
            "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": top[0][0]},
                         "logprobs": {"content": [{**entries[0], "top_logprobs": entries}]}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 1, "total_tokens": prompt_tokens + 1}}


class FakeOpenAI:
    def __init__(self, top, delay_s: float = 0.0):
        self.top, self.delay_s, self.bodies = top, delay_s, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        time.sleep(self.delay_s)
        return httpx.Response(200, json=completion(self.top))


def pool(sf) -> list[str]:
    return sorted(t for t in sf.types if t not in sf.unoffered_types)


def test_openai_verdict_then_cache_hit_without_network(sf, tmp_path):
    cache = tmp_path / "openai.json"
    keys = option_keys({t: "" for t in pool(sf)})
    ear = next(t for t in pool(sf) if "Hearing" in sf.types[t].name)
    fake = FakeOpenAI([(keys[ear], -0.01), ("1", -6.0)])
    client = OpenAIChoiceClient("k", mode="auto", cache_path=cache, transport=httpx.MockTransport(fake))
    verdict = JevTypeDisambiguator(sf, client).pick_type("ringing in both my ears", None, pool(sf))
    client.save()
    assert verdict.act == ear
    assert len(fake.bodies) == 1 and fake.bodies[0]["model"] == "gpt-4o-mini"
    [call] = client.calls
    assert (call.source, call.input_tokens, call.output_tokens) == ("live", 700, 1)
    assert call.usd == pytest.approx(700 * 0.15e-6 + 0.6e-6)

    def offline(request):
        raise AssertionError("cache mode must not touch the network")

    again = OpenAIChoiceClient(None, mode="cache", cache_path=cache, transport=httpx.MockTransport(offline))
    assert JevTypeDisambiguator(sf, again).pick_type("ringing in both my ears", None, pool(sf)) == verdict
    assert [c.source for c in again.calls] == ["cache"]


def test_openai_timeout_declines(sf):
    fake = FakeOpenAI([("1", -0.01)], delay_s=1.0)
    client = OpenAIChoiceClient("k", mode="live", timeout_s=0.2, turn_budget_s=0.2, transport=httpx.MockTransport(fake))
    client.begin_turn()
    started = time.perf_counter()
    verdict = JevTypeDisambiguator(sf, client).pick_type("ringing in both my ears", None, pool(sf))
    assert verdict == DECLINE
    assert time.perf_counter() - started < 0.6
    assert [c.source for c in client.calls] == ["failed"]
    client.close()


def test_openai_spend_cap_aborts_the_run(sf):
    fake = FakeOpenAI([("1", -0.01)])
    client = OpenAIChoiceClient("k", mode="auto", live_limit=1, transport=httpx.MockTransport(fake))
    hook = JevTypeDisambiguator(sf, client)
    hook.pick_type("ringing in my ears", None, pool(sf))
    hook.pick_type("ringing in my ears", None, pool(sf))  # memo: not a request
    with pytest.raises(SpendCapExceeded):
        hook.pick_type("itchy rash", None, pool(sf))
    assert len(fake.bodies) == 1


# ---- embeddings -----------------------------------------------------------------------------

class TinyEmbedder:
    """2-d vectors by keyword: 'ear' words point one way, 'skin' words the other."""

    def embed(self, texts):
        def vec(t):
            t = t.lower()
            v = np.array([1.0 if any(w in t for w in ("ear", "hearing", "ent")) else 0.05,
                          1.0 if any(w in t for w in ("skin", "rash", "derma")) else 0.05])
            return v / np.linalg.norm(v)
        return np.stack([vec(t) for t in texts])


def test_embed_chooser_acts_on_a_clear_match_and_temperature_flattens_it(sf):
    ids = [t for t in pool(sf) if sf.types[t].specialty in ("ENT", "Dermatology")]
    ear = [t for t in ids if sf.types[t].specialty == "ENT"]
    sharp = EmbedChooser(sf, EmbedClient(TinyEmbedder(), temperature=0.01))
    flat = EmbedChooser(sf, EmbedClient(TinyEmbedder(), temperature=10.0))
    one_ear = [ear[0], *[t for t in ids if t not in ear]]
    assert sharp.pick_type("ringing in my ears", None, one_ear).act == ear[0]
    assert flat.pick_type("ringing in my ears", None, one_ear).act is None
    assert flat.pick_type("ringing in my ears", None, one_ear).called


def test_embed_chooser_probabilities_are_a_softmax_over_cosines(sf):
    client = EmbedClient(TinyEmbedder(), temperature=0.5)
    probs = client.probabilities("type", "a rash", {"x": "skin check", "y": "hearing test"})
    q, x, y = TinyEmbedder().embed(["a rash", "skin check", "hearing test"])
    sx, sy = x @ q, y @ q
    assert probs["x"] == pytest.approx(math.exp(sx / 0.5) / (math.exp(sx / 0.5) + math.exp(sy / 0.5)), rel=1e-6)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert [(c.key, c.source, c.usd) for c in client.calls] == [("type", "live", 0.0)]


def test_a_broken_embedder_declines(sf):
    class Broken:
        def embed(self, texts):
            raise RuntimeError("onnx failed")

    hook = EmbedChooser(sf, EmbedClient(Broken()))
    assert hook.pick_type("ringing in my ears", None, pool(sf)[:3]) == DECLINE
    assert [c.source for c in hook.client.calls] == ["failed"]


# ---- agent schema -----------------------------------------------------------------------------

@pytest.mark.parametrize("resolver, chooser, timeout_ms", [
    ({}, "jev", 2500),
    ({"jev": {"enabled": True, "timeout_ms": 1200}}, "jev", 1200),
    ({"jev": {"enabled": False}}, "none", 2500),
    ({"chooser": "openai", "jev": {"enabled": False, "timeout_ms": 1200}}, "openai", 1200),
    ({"chooser": "embed", "timeout_ms": 900}, "embed", 900),
])
def test_resolver_chooser_is_backward_compatible(resolver, chooser, timeout_ms):
    cfg = ResolverConfig.from_dict(resolver)
    assert (cfg.chooser, cfg.timeout_ms) == (chooser, timeout_ms)


def test_bad_chooser_and_timeout_are_path_errors():
    agent = json.loads((resolve_catalog_path("agents/clinic-scheduler.json")).read_text(encoding="utf-8"))
    agent["resolver"] = {"chooser": "gpt5", "timeout_ms": 0}
    assert [e["path"] for e in validate_agent(agent)] == ["resolver.chooser", "resolver.timeout_ms"]
    agent["resolver"] = {"chooser": "embed"}
    assert validate_agent(agent) == []


# ---- per-call wiring --------------------------------------------------------------------------

@pytest.mark.parametrize("chooser, env, provider, hook", [
    ("jev", {"CMD_API_KEY": "k"}, "jev", JevTypeDisambiguator),
    ("jev", {}, "none", NoDisambiguator),
    ("openai", {"OPENAI_API_KEY": "k"}, "openai", JevTypeDisambiguator),
    ("openai", {}, "none", NoDisambiguator),
    ("embed", {}, "embed", EmbedChooser),
    ("none", {"CMD_API_KEY": "k", "OPENAI_API_KEY": "k"}, "none", NoDisambiguator),
])
def test_context_builds_the_chosen_model(monkeypatch, chooser, env, provider, hook):
    for key in ("CMD_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    ctx = make_context("data/catalog.json", speak_direct=True, chooser=chooser, timeout_ms=900)
    assert ctx.provider == provider and ctx.chooser == chooser
    assert isinstance(ctx.disambiguator._types, hook)
    if provider == "openai":
        assert isinstance(ctx.model_client, OpenAIChoiceClient)
        assert (ctx.model_client.timeout_s, ctx.model_client.turn_budget_s) == (0.9, 0.9)
    if provider == "jev":
        assert isinstance(ctx.model_client, JevClient) and ctx.model_client.timeout_s == 0.9
    if provider == "embed":
        assert isinstance(ctx.model_client.embedder, FastEmbedder)

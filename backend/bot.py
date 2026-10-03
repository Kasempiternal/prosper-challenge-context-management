#
# Voice pipeline — Prosper AI Software Engineer Challenge
#
# The runnable voice agent: WebRTC transport + ElevenLabs STT/TTS + OpenAI LLM,
# driven by a Pipecat Flows node graph. This file is generic — it loads an agent
# definition (JSON) via AgentBuilder and runs it. Swapping the agent is a data
# change, not a code change.
#
#   /start body {agent | agent_id, api_keys?}  ->  AgentBuilder  ->  Pipecat Flows graph  ->  FlowManager
#
# api_keys (OPENAI_API_KEY, ELEVENLABS_API_KEY, CMD_API_KEY) are the browser's keys: each overrides
# .env for this call only (call_key). The same process serves the agents REST API (agents_api.py)
# and the API key endpoints (api_keys.py) on the runner's app.
#
# Run:  python bot.py   then open http://localhost:7860/client
#

import asyncio
import json
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIFunctionCallReportLevel,
    RTVIObserverParams,
    RTVIServerMessageFrame,
)
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from pipecat.flows import FlowManager

from agent_builder import AgentBuilder, AgentConfig, validate_agent
from agent_tools import resolver_mode_event, stt_keyterms, warm_up_model
from agent_tools.context import preload_catalogs
from agents_api import ID_RE, agents_dir_from_env, create_router
from api_keys import (
    CALL_ENVS,
    ELEVENLABS_ENV,
    OPENAI_ENV,
    call_key,
    create_keys_router,
    install_log_redaction,
    take_start_keys,
)

# Load .env next to this file, so the bot runs the same from the repo root or backend/.
load_dotenv(Path(__file__).parent / ".env", override=True)

# Fallback agent when the /start body names none.
AGENT_FLOW = Path(__file__).parent / "example_flow.json"

install_log_redaction()
app.include_router(create_router())
app.include_router(create_keys_router())
preload_catalogs(agents_dir_from_env())


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


class SerialToolCallsLLMService(OpenAILLMService):
    """One tool call per LLM turn: a speak-direct handler returns NO_RESPONSE, which only
    holds if no sibling call asks the LLM to run again. OpenAI rejects parallel_tool_calls
    on a request without tools (end nodes), so it is set per request."""

    def build_chat_completion_params(self, params_from_context) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        if params.get("tools"):
            params["parallel_tool_calls"] = False
        return params


# Watchdog that ends a user turn after this long with no VAD or transcript activity. Pipecat's 5 s
# default turned one stray turn into 5 s of dead air; 2 s stays well above STT latency (~0.4 s p99).
USER_TURN_STOP_TIMEOUT_S = 2.0


def user_aggregator_params() -> LLMUserAggregatorParams:
    """A user turn starts only on Silero VAD speech. Pipecat's default also starts one on any
    transcript, so a transcript landing after the turn ended opened an empty turn, interrupted
    the reply being generated, and waited out the stop timeout. Stop stays the default smart-turn
    analyzer."""
    return LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
        user_turn_strategies=UserTurnStrategies(start=[VADUserTurnStartStrategy()]),
        user_turn_stop_timeout=USER_TURN_STOP_TIMEOUT_S,
    )


def make_services(config: AgentConfig, api_keys: Mapping[str, str], keyterms: list[str] | None):
    """The call's STT, TTS and LLM, each with this call's key over .env (call_key)."""
    elevenlabs_key = call_key(ELEVENLABS_ENV, api_keys)
    stt = ElevenLabsRealtimeSTTService(
        api_key=elevenlabs_key,
        settings=ElevenLabsRealtimeSTTService.Settings(keyterms=keyterms),
    )
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        settings=ElevenLabsTTSService.Settings(voice=config.voice_id),
    )
    llm = SerialToolCallsLLMService(
        api_key=call_key(OPENAI_ENV, api_keys),
        settings=SerialToolCallsLLMService.Settings(model=config.model),
    )
    return stt, tts, llm


def load_agent_data(body: dict | None) -> dict:
    """Pick the session's agent: inline `agent`, stored `agent_id`, else the example."""
    body = body or {}
    if "agent" in body:
        return body["agent"]
    if "agent_id" in body:
        agent_id = body["agent_id"]
        if not isinstance(agent_id, str) or not ID_RE.match(agent_id):
            raise ValueError(f"Invalid agent_id {agent_id!r}")
        path = agents_dir_from_env() / f"{agent_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(AGENT_FLOW.read_text(encoding="utf-8"))


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, agent_data: dict,
                  api_keys: Mapping[str, str]) -> None:
    config = AgentConfig.from_dict(agent_data)
    logger.info(f"Starting '{config.name}' with {len(config.nodes)} nodes")

    async def send_event(event: dict) -> None:
        await worker.queue_frame(RTVIServerMessageFrame(data=event))

    builder = AgentBuilder(config, on_event=send_event, api_keys=api_keys)
    keyterms = stt_keyterms(builder.tool_context.index) if builder.tool_context else None

    stt, tts, llm = make_services(config, api_keys, keyterms)

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=user_aggregator_params(),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    # RTVI is enabled by default, so the worker's RTVIObserver turns this frame
    # into an RTVI "server-message" for the client.
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        # Tool names reach the Dev view; arguments and results (patient details) do not.
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"*": RTVIFunctionCallReportLevel.NAME}
        ),
    )

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=context_aggregator,
        worker=worker,
        transport=transport,
    )

    background: set[asyncio.Task] = set()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — starting flow at initial node")
        if builder.tool_context:
            await send_event(resolver_mode_event(builder.tool_context))
        if builder.tool_context and builder.tool_context.model_client:
            task = asyncio.create_task(warm_up_model(builder.tool_context))
            background.add(task)
            task.add_done_callback(background.discard)
        await builder.start(flow_manager)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        for task in list(background):
            task.cancel()
        if builder.tool_context and builder.tool_context.model_client:
            builder.tool_context.model_client.close()
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner (and Pipecat Cloud)."""
    api_keys = take_start_keys(runner_args.body)
    missing = [env for env in CALL_ENVS if not call_key(env, api_keys)]
    try:
        agent_data = load_agent_data(runner_args.body)
        errors = validate_agent(agent_data)
    except (OSError, ValueError) as e:
        errors = [{"path": "", "message": str(e)}]
    reason = (f"no {' or '.join(missing)} in .env or the request" if missing
              else f"invalid agent: {errors}" if errors else "")
    if reason:
        logger.error(f"Not starting session {runner_args.session_id}: {reason}")
        connection = getattr(runner_args, "webrtc_connection", None)
        if connection is not None:
            await connection.disconnect()
        return

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, agent_data, api_keys)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()

#
# Voice pipeline — Prosper AI Software Engineer Challenge
#
# The runnable voice agent: WebRTC transport + ElevenLabs STT/TTS + OpenAI LLM,
# driven by a Pipecat Flows node graph. This file is generic — it loads an agent
# definition (JSON) via AgentBuilder and runs it. Swapping the agent is a data
# change, not a code change.
#
#   /start body {agent | agent_id}  ->  AgentBuilder  ->  Pipecat Flows graph  ->  FlowManager
#
# The same process serves the agents REST API (agents_api.py) on the runner's app.
#
# Run:  python bot.py   then open http://localhost:7860/client
#

import json
import os
from pathlib import Path

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
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner
from pipecat_flows import FlowManager

from agent_builder import AgentBuilder, AgentConfig, validate_agent
from agents_api import ID_RE, agents_dir_from_env, create_router

# Load .env next to this file, so the bot runs the same from the repo root or backend/.
load_dotenv(Path(__file__).parent / ".env", override=True)

# Fallback agent when the /start body names none.
AGENT_FLOW = Path(__file__).parent / "example_flow.json"

app.include_router(create_router())


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


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


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, agent_data: dict) -> None:
    config = AgentConfig.from_dict(agent_data)
    logger.info(f"Starting '{config.name}' with {len(config.nodes)} nodes")

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(voice=config.voice_id),
    )
    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model=config.model)

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
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
    )

    async def send_event(event: dict) -> None:
        await worker.queue_frame(RTVIServerMessageFrame(data=event))

    builder = AgentBuilder(config, on_event=send_event)

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=context_aggregator,
        worker=worker,
        transport=transport,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — starting flow at initial node")
        await builder.start(flow_manager)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner (and Pipecat Cloud)."""
    try:
        agent_data = load_agent_data(runner_args.body)
        errors = validate_agent(agent_data)
    except (OSError, ValueError) as e:
        errors = [{"path": "", "message": str(e)}]
    if errors:
        logger.error(f"Not starting session {runner_args.session_id}: invalid agent: {errors}")
        connection = getattr(runner_args, "webrtc_connection", None)
        if connection is not None:
            await connection.disconnect()
        return

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, agent_data)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()

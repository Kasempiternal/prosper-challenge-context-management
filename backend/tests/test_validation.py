import pytest

from agent_builder import AgentBuilder, AgentValidationError, validate_agent


def test_example_flow_is_valid(example_agent):
    assert validate_agent(example_agent) == []


def test_unknown_target(example_agent):
    example_agent["nodes"][0]["edges"][0]["target"] = "nowhere"
    assert validate_agent(example_agent) == [
        {"path": "nodes[0].edges[0].target", "message": "Edge targets unknown node 'nowhere'."}
    ]


def test_duplicate_node_names(example_agent):
    example_agent["nodes"][2]["name"] = "greeting"
    errors = validate_agent(example_agent)
    assert {"path": "nodes[2].name", "message": "Duplicate node name 'greeting'."} in errors


def test_bad_function_name(example_agent):
    example_agent["nodes"][1]["edges"][0]["function"] = "record details!"
    assert [e["path"] for e in validate_agent(example_agent)] == ["nodes[1].edges[0].function"]


def test_duplicate_function_in_node(example_agent):
    edges = example_agent["nodes"][0]["edges"]
    edges.append(dict(edges[0]))
    assert validate_agent(example_agent) == [
        {
            "path": "nodes[0].edges[1].function",
            "message": "Duplicate function 'choose_intent' in this node.",
        }
    ]


def test_required_not_in_properties(example_agent):
    example_agent["nodes"][1]["edges"][0]["required"] = ["full_name", "phone"]
    assert validate_agent(example_agent) == [
        {
            "path": "nodes[1].edges[0].required",
            "message": "Required fields not in properties: phone.",
        }
    ]


def test_non_end_node_without_edges(example_agent):
    example_agent["nodes"][2]["edges"] = []
    assert [e["path"] for e in validate_agent(example_agent)] == ["nodes[2].edges"]


def test_empty_task_message_on_non_end_node(example_agent):
    example_agent["nodes"][0]["task_messages"][0]["content"] = "  "
    assert [e["path"] for e in validate_agent(example_agent)] == [
        "nodes[0].task_messages[0].content"
    ]


def test_collects_all_errors(example_agent):
    example_agent["name"] = ""
    example_agent["initial_node"] = "missing"
    example_agent["nodes"][0]["edges"][0]["target"] = "nowhere"
    assert [e["path"] for e in validate_agent(example_agent)] == [
        "name",
        "initial_node",
        "nodes[0].edges[0].target",
    ]


def test_missing_keys_reported_not_raised():
    assert [e["path"] for e in validate_agent({"nodes": [{}]})] == [
        "name",
        "nodes[0].name",
        "initial_node",
        "nodes[0].task_messages",
        "nodes[0].edges",
    ]


def test_extra_keys_are_tolerated(example_agent):
    example_agent["id"] = "prosper-scheduler"
    example_agent["future"] = {"x": 1}
    example_agent["nodes"][0]["ui"] = {"x": 0, "y": 0}
    example_agent["nodes"][0]["context_strategy"] = "reset"
    example_agent["nodes"][0]["edges"][0]["guard"] = "always"
    assert validate_agent(example_agent) == []
    assert AgentBuilder.from_dict(example_agent).config.name == "Prosper Scheduler"


def test_builder_raises_with_all_errors(example_agent):
    example_agent["initial_node"] = "missing"
    example_agent["nodes"][0]["edges"][0]["target"] = "nowhere"
    with pytest.raises(AgentValidationError) as exc:
        AgentBuilder.from_dict(example_agent)
    assert [e["path"] for e in exc.value.errors] == ["initial_node", "nodes[0].edges[0].target"]

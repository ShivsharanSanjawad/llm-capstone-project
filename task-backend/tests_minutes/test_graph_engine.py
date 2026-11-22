from graph_engine import analyze_task_graph


def test_reports_direct_and_indirect_cycles_with_dependency_evidence():
    tasks = [
        {
            "task_id": "T1",
            "description": "Publish the API",
            "explicit_prerequisites": ["T3"],
            "inferred_prerequisites": [],
            "dependency_evidence": [
                {"prerequisite_task_id": "T3", "task_id": "T1", "evidence": "T1 needs T3's build."},
            ],
        },
        {
            "task_id": "T2",
            "description": "Integrate the API",
            "explicit_prerequisites": ["T1"],
            "inferred_prerequisites": [],
            "dependency_evidence": [
                {"prerequisite_task_id": "T1", "task_id": "T2", "evidence": "T2 starts after T1 publishes."},
            ],
        },
        {
            "task_id": "T3",
            "description": "Build the API",
            "explicit_prerequisites": ["T2", "T1"],
            "inferred_prerequisites": [],
            "dependency_evidence": [
                {"prerequisite_task_id": "T2", "task_id": "T3", "evidence": "T3 waits for T2's integration."},
                {"prerequisite_task_id": "T1", "task_id": "T3", "evidence": "T3 needs T1's published contract."},
            ],
        },
    ]

    result = analyze_task_graph(tasks)

    assert result["is_feasible"] is False
    assert {frozenset(cycle["task_ids"][:-1]) for cycle in result["cycle_details"]} == {
        frozenset({"T1", "T3"}),
        frozenset({"T1", "T2", "T3"}),
    }
    for cycle in result["cycle_details"]:
        assert cycle["task_ids"][0] == cycle["task_ids"][-1]
        assert all(edge["evidence"] for edge in cycle["dependencies"])


def test_cycle_evidence_can_come_from_contextual_cycle_records():
    tasks = [
        {"task_id": "T1", "explicit_prerequisites": ["T2"], "inferred_prerequisites": []},
        {"task_id": "T2", "explicit_prerequisites": ["T1"], "inferred_prerequisites": []},
    ]
    contextual_data = {
        "initial_circular_dependencies": [
            {"from_task_id": "T2", "to_task_id": "T1", "description": "T1 needs T2's report."},
            {"from_task_id": "T1", "to_task_id": "T2", "description": "T2 needs T1's review."},
        ],
    }

    result = analyze_task_graph(tasks, contextual_data)

    assert result["cycle_details"][0]["task_ids"][0] == result["cycle_details"][0]["task_ids"][-1]
    assert {edge["evidence"] for edge in result["cycle_details"][0]["dependencies"]} == {
        "T1 needs T2's report.",
        "T2 needs T1's review.",
    }

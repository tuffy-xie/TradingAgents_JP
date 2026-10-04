"""Reaccept saved production evidence offline through the sole publication path.

Example: python scripts/replay_saved_states.py --state saved.json --output output/replays
This never executes a graph or regenerates reasoning. Source files are immutable.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
ATTEMPTS: list[str] = []


def denied(*args, **kwargs):
    ATTEMPTS.append("OFFLINE_NETWORK_OR_LLM_ATTEMPT")
    raise RuntimeError("Saved-state replay cannot use network or LLM")


def install_offline_guard():
    # Prevent package import's normal loader from consulting user credentials.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    socket.socket.connect = socket.socket.connect_ex = denied
    socket.create_connection = socket.getaddrinfo = denied
    from curl_cffi import requests
    from langchain_core.language_models import BaseChatModel
    BaseChatModel.invoke = BaseChatModel.ainvoke = denied
    BaseChatModel.stream = BaseChatModel.astream = denied
    requests.Session.request = requests.AsyncSession.request = denied


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    install_offline_guard()
    from bs4 import BeautifulSoup

    from tradingagents import final_output as f
    from tradingagents.agents.execution_validation import EXECUTION_PLAN_FIELDS
    from tradingagents.reporting import write_report_tree
    from tradingagents.secret_redaction import sanitize_data, sanitize_text
    from web.server import _render_report_html

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True))
    summaries = []
    for source in args.state:
        source_bytes = source.read_bytes()
        original = json.loads(source_bytes)
        manifest = original["run_manifest"]
        run_id = manifest["run_id"]
        # Exercise today's acceptance logic even when the archived acceptance
        # used the same semantic revision. Restore only preserved raw reasoning,
        # not new reasoning, and leave the evidence/bundle/manifest untouched.
        candidate = copy.deepcopy(original)
        candidate.update(copy.deepcopy(candidate.get("raw_agent_outputs") or {}))
        candidate.pop("final_output_contract", None)
        candidate.pop("accepted_report_markdown", None)
        state = f.build_canonical_final_state(candidate)
        assert sanitize_data(state) == state, "State contains unredacted data"
        contract = state["final_output_contract"]
        text = state.get("accepted_report_markdown", "")
        assert contract["status"] == "FINALIZED", contract["artifact_issues"]
        assert contract["audit_closure_status"] == "CLOSED"
        assert contract["semantic_revision"] == f._CONTRACT_SEMANTIC_REVISION
        assert not contract["artifact_issues"] and all(contract["validation_dimensions"].values())
        allowed = contract["execution_allowed"]
        unauthorized = f._artifact_without_validated_execution(state, text) if allowed else text
        scans = {
            "secondary_internal_recommendation": len(f._rating_artifact_claims(state, text)),
            "unauthorized_execution": len(f._artifact_execution_claims(unauthorized)),
            "process_narration": len(f._generation_process_claims(text)),
            "market_authority_violations": len(f._artifact_market_claims(state, text)),
            "evidence_gate_violations": len(f._artifact_evidence_gate_findings(state, text)),
            "unresolved": sum(e.get("resolution") == "UNRESOLVED" for e in state["evidence_audit"]),
            "withheld_execution_parameters": 0 if allowed else sum(
                state["validated_execution"].get(key) is not None for key in EXECUTION_PLAN_FIELDS),
        }
        issues = f._validate_final_artifact(state, allowed, accepted_report=text)
        _, closure = f._finalize_audit(state["evidence_audit"], state, accepted_report=text, execution_allowed=allowed)
        assert not any(scans.values()) and not issues and not closure, (scans, issues, closure)
        digest = sha(text.encode("utf-8"))
        assert digest == contract["accepted_report_sha256"]
        for finding in state["evidence_audit"]:
            if finding.get("original_claim") and finding.get("claim_sha256"):
                assert sha(finding["original_claim"].encode("utf-8")) == finding["claim_sha256"]
            if finding.get("accepted_artifact_sha256"):
                assert finding["accepted_artifact_sha256"] == digest
        # Acceptance cannot modify original source facts to get a pass.
        expected_source = f._drop_deprecated_horizon_metadata(copy.deepcopy(original))
        assert state.get("japan_data_bundle") == expected_source.get("japan_data_bundle")
        assert f.canonical_market_authority(state) == f.canonical_market_authority(original)
        assert state["run_manifest"] == manifest

        directory = args.output / f"{run_id[:8]}-{head[:7]}{'-working' if dirty else ''}"
        directory.mkdir(parents=True, exist_ok=True)
        write_report_tree(state, "6981.T", directory)
        file_sha = sha((directory / "complete_report.md").read_bytes())
        assert file_sha == digest
        html = _render_report_html(state, auto_print=False)
        dom = BeautifulSoup(html, "html.parser")
        body = dom.select_one(".canonical-report")
        expected = BeautifulSoup(f.render_markdown_fragment(text), "html.parser")
        assert body and body.get_text(" ", strip=True) == expected.get_text(" ", strip=True)
        assert [t.get_text(" ", strip=True) for t in body.find_all("table")] == [
            t.get_text(" ", strip=True) for t in expected.find_all("table")]
        assert not f.validate_rendered_html(html)
        assert "file://" not in html and str(REPO) not in html
        for tag in dom.find_all(True):
            for value in tag.attrs.values():
                for item in value if isinstance(value, list) else [value]:
                    assert sanitize_text(str(item)) == str(item)
            for node in tag.find_all(string=True, recursive=False):
                assert sanitize_text(str(node)) == str(node)
        (directory / "accepted_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "complete_report.html").write_text(html, encoding="utf-8")
        summary = {
            "source_run_id": run_id, "source_run_head": manifest["git_head"],
            "source_state_path": str(source.resolve()), "source_state_sha256": sha(source_bytes),
            "retired_horizon_metadata_removed": f._has_deprecated_horizon_state(original),
            "replay_code_head": head, "replay_code_dirty": dirty,
            "result": "OFFLINE_CONTRACT_PASS", "manual_semantic_review": "PENDING",
            "market_authority": f.canonical_market_authority(state),
            "validated_execution": state["validated_execution"], "final_output_contract": contract,
            "exact_artifact_scans": scans, "exact_validation_issues": issues, "audit_closure_issues": closure,
            "audit_categories": dict(Counter(e.get("category") for e in state["evidence_audit"])),
            "audit_resolutions": dict(Counter(e.get("resolution") for e in state["evidence_audit"])),
            "sha": {"contract": contract["accepted_report_sha256"], "state": digest, "file": file_sha},
            "claim_sha_errors": 0, "artifact_sha_errors": 0,
            "shared_renderer_dom_consistent": True, "source_unchanged": source.read_bytes() == source_bytes,
            "fresh_graph_starts": 0, "llm_calls": 0, "network_attempts": len(ATTEMPTS), "pdf_generated": False,
        }
        assert summary["source_unchanged"] and not ATTEMPTS
        assert sanitize_data(summary) == summary
        (directory / "replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "acceptance_report.md").write_text(
            f"# Offline migration replay\n\nSource run: {run_id}\n\nSource HEAD: {manifest['git_head']}\n\n"
            f"Replay HEAD: {head}; dirty: {dirty}\n\nContract: FINALIZED / CLOSED\n\n"
            f"Exact-byte SHA: {digest}\n\nExact scans: {scans}\n\n"
            "Shared HTML DOM/table parity verified; source bundle, authority, manifest and bytes unchanged.\n\n"
            "No fresh graph, LLM, network, browser or PDF. Manual review is recorded separately.\n",
            encoding="utf-8")
        summaries.append({"run": run_id, "directory": str(directory.resolve()), "sha": digest, "scans": scans})
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

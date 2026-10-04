"""Live benchmark: short tasks on public test sites, dummy data only. Not run by pytest.

Uses whatever decision backend `.env` selects (DECISION_BACKEND=ollama for nimble) and logs
per-step operation/target probabilities. Example:
    uv run python scripts/bench_ollama.py --suite all --reps 2
"""

import argparse
import json
import logging
import time
from pathlib import Path

from jev_ultrafast import mcp_server

WIKI = "https://en.wikipedia.org/wiki/Main_Page"
FORM = "https://httpbin.org/forms/post"
QUOTES = "https://quotes.toscrape.com/"
BOOKS = "https://books.toscrape.com/"
INTERNET = "https://the-internet.herokuapp.com/"

# (name, url, goal, check) — check receives the final result dict.
TUNED = [
    ("search", WIKI, "Search Wikipedia for Ada Lovelace and open her article", lambda r: "Ada_Lovelace" in r["url"]),
    (
        "form",
        FORM,
        "Fill the order form: customer name Test User, telephone 5550100, email test@example.com, "
        "size Medium, topping Bacon, then submit the order",
        lambda r: '"custname": "Test User"' in r["text"] and '"size": "medium"' in r["text"] and "bacon" in r["text"],
    ),
    (
        "open_result",
        BOOKS,
        "Open the product page for the book A Light in the Attic",
        lambda r: "a-light-in-the-attic" in r["url"],
    ),
    ("dropdown", INTERNET + "dropdown", "Select Option 2 in the dropdown", lambda r: r["select"] == "2"),
    ("pagination", QUOTES, "Go to the third page of quotes", lambda r: r["url"].rstrip("/").endswith("/page/3")),
]
HOLDOUT = [
    (
        "h_search",
        WIKI,
        "Search Wikipedia for Dart (programming language) and open its article",
        lambda r: "Dart_(programming_language)" in r["url"],
    ),
    (
        "h_form",
        FORM,
        "Order a Large pizza with Cheese and Onion toppings for customer Demo Person, telephone 5550199, "
        "email demo@example.com, delivery instructions Ring twice, then submit",
        lambda r: all(s in r["text"] for s in ('"size": "large"', "cheese", "onion", "Ring twice")),
    ),
    ("h_open", QUOTES, "Open the author page for Albert Einstein", lambda r: "Albert-Einstein" in r["url"]),
    ("h_category", BOOKS, "Open the Travel book category", lambda r: "travel_2" in r["url"]),
    (
        "h_checkbox",
        INTERNET + "checkboxes",
        "Check checkbox 1 so both checkboxes are checked",
        lambda r: r["checks"] == [True, True],
    ),
]
SUITES = {"tuned": TUNED, "holdout": HOLDOUT, "all": TUNED + HOLDOUT}


def top(probabilities, count):
    return [(key, round(value, 2)) for key, value in sorted(probabilities.items(), key=lambda kv: -kv[1])[:count]]


def run(url, goal, max_steps, verbose):
    from jev_ultrafast.agent import Agent

    started = time.perf_counter()
    error = None
    with Agent(url, goal) as agent:
        seen = 0
        try:
            for state in agent.run():
                if verbose:
                    labels = {e["index"]: e["label"][:30] for e in state["elements"]}
                    for d in state["decisions"][seen:]:
                        operations = top(d["operation_probabilities"], 3)
                        targets = top(d["target_probabilities"], 3)
                        targets = [(k, labels.get(k.split(":")[0], "?"), p) for k, p in targets]
                        print(f"    {d['operation']} {operations} {targets} {d['latency_ms']}ms")
                seen = len(state["decisions"])
                if len(state["history"]) >= max_steps:
                    break
        except Exception as exc:  # A failed run is a benchmark result, not a crash.
            error = str(exc)
        final = agent.snapshot()
        decisions = final["decisions"]
        evaluate = agent.browser.evaluate
        return {
            "status": final["status"],
            "error": error,
            "url": final["page"]["url"],
            "steps": [(h["action"][:40], h["text"]) for h in final["history"]],
            "decisions": len(decisions),
            "mean_decision_ms": round(sum(d["latency_ms"] for d in decisions) / max(1, len(decisions))),
            "wall_s": round(time.perf_counter() - started, 1),
            "text": evaluate("document.body.innerText.slice(0, 3000)"),
            "checks": evaluate("[...document.querySelectorAll('input[type=checkbox]')].map(c => c.checked)"),
            "select": evaluate("document.querySelector('select')?.value ?? null"),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=SUITES, default="all")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="task names to run")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--quiet", action="store_true", help="hide per-step probabilities")
    parser.add_argument("--output", type=Path, help="write results JSON here")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp_server.load_env()
    mcp_server.ensure_model()
    mcp_server.ensure_chrome()
    results = []
    for name, url, goal, check in SUITES[args.suite]:
        if args.only and name not in args.only:
            continue
        for rep in range(args.reps):
            print(f"== {name} #{rep + 1}", flush=True)
            result = run(url, goal, args.max_steps, not args.quiet)
            ok = result["error"] is None and check(result)
            print(
                f"  -> {'PASS' if ok else 'FAIL'} status={result['status']} steps={len(result['steps'])} "
                f"wall={result['wall_s']}s decision={result['mean_decision_ms']}ms url={result['url']}",
                flush=True,
            )
            if not ok:
                print(f"     {result['error'] or result['steps']}", flush=True)
            result.pop("text")
            results.append({"task": name, "ok": ok, **result})
    if not results:
        raise SystemExit("No tasks matched")
    passed = sum(r["ok"] for r in results)
    walls = sorted(r["wall_s"] for r in results)
    print(f"SUCCESS {passed}/{len(results)} = {passed / len(results):.0%}; median wall {walls[len(walls) // 2]}s")
    if args.output:
        args.output.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()

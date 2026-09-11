"""
Minimal agent loop with turn_guard, using the official Anthropic SDK (pip install anthropic).
Shows where the three rules go. Adapt run_tool() to your own tools.
"""
import anthropic

import turn_guard

client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY
MODEL = "claude-opus-5"
TOOLS = []                              # your tool definitions


def run_tool(name, args):
    raise NotImplementedError


def chat(history, user_text):
    # Rule 3: the only way a "user" message enters history is here, from the real input channel.
    history.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
    while True:
        resp = client.messages.create(
            model=MODEL, max_tokens=4096, messages=history, tools=TOOLS,
            stop_sequences=turn_guard.stop_sequences("prose"),      # Rule 1 ("code" for coding agents)
        )
        blocks = [b.model_dump(exclude_none=True) for b in resp.content]
        verdict = turn_guard.inspect(blocks)                         # Rule 2
        if resp.stop_reason == "stop_sequence":
            print(f"[turn-guard] generation stopped at {resp.stop_sequence!r}")
        if not verdict.ok:
            v = verdict.violations[0]
            print(f"[turn-guard] cut a fake user line: {v.line!r}; "
                  f"skipped {len(verdict.dropped_tool_calls)} tool call(s)")
            # Store only the cut version, so the fake line never comes back as context.
            history.append({"role": "assistant", "content": verdict.blocks or [{"type": "text", "text": "…"}]})
            return verdict.blocks
        history.append({"role": "assistant", "content": verdict.blocks})
        calls = [b for b in verdict.blocks if b["type"] == "tool_use"]
        if not calls:
            return verdict.blocks
        history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c["id"], "content": str(run_tool(c["name"], c["input"]))}
            for c in calls]})

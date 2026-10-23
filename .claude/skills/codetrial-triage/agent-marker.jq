# A commit message that credits an AI agent, never a person: a human co-author
# keeps the credit (codetrial-conventions). Matched on the address agents commit
# with, never on a name, since a person may well be called Claude. Its own file
# so the tests check every address in one jq call; hygiene.sh includes it.
def ai_marker: test("(^|\n)co-authored-by:[^\n]*<(noreply@anthropic\\.com|codex@openai\\.com|cursoragent@cursor\\.com|([0-9]+\\+)?(copilot|(copilot-swe-agent|devin-ai-integration|claude|cursor|codex|gemini-code-assist|openhands-agent)\\[bot\\])@users\\.noreply\\.github\\.com|copilot@github\\.com)>|(^|\n)[^a-z\n]*generated (with|by) \\[?(claude code|codex|github copilot|cursor|gemini)"; "i");

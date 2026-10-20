"""The walkthrough prompt.

Framing rules (from hard-won eval experience): the task is presented as exactly what it
is — a pre-release documentation review — with no mention of measurement machinery; the
agent must invoke operations directly (subagents lack MCP/CLI context, memory 566); the
report contract embeds the canonical example from report.py so prompt and parser cannot
drift.
"""
from test_harness.report import report_contract_example

_TEMPLATE = """\
You are doing a pre-release documentation review of an agent skill for Forgetful, a
knowledge-base system. The skill under review is ./SKILL.md in your working directory.

Your job is to work through the skill for real, the way an agent following it would:

- Read ./SKILL.md carefully.
- Follow its guidance step by step against the live system it describes. The `forgetful`
  CLI is installed and already connected to a live Forgetful server (FORGETFUL_SERVER is
  set for you). Pass --json when calling it.
- Actually run the operations the skill teaches and check each behaves as documented.
  Where a step is judgment rather than a command, exercise the judgment on a realistic
  example of your own and note whether the guidance was sufficient.
- If a step cannot be run safely from here (for example it would install software or
  modify user configuration), review it by inspection instead and say so in your report.

Ground rules:

- Invoke every operation directly yourself. Do not delegate to subagents or background
  tasks.
- Use the server you are connected to: do not pass --local or --server flags to the
  forgetful CLI.
- Stay inside the working directory; put any scratch files here.
- Do not install anything or modify user configuration.
{note}
When you have worked through the whole skill, write your findings to report.json in the
working directory as your final action, using exactly this shape:

{contract}

verdict is "pass" if everything worked as documented, "issues" if it worked but has
problems worth fixing, "blocked" if you could not complete the walkthrough. Every issue
needs evidence — the command you ran and what came back. An empty issues list with
verdict "pass" is a legitimate outcome; do not invent problems.
"""

_PER_SKILL_NOTES = {
    "forgetful-cli-setup": (
        "Note for this skill: the CLI is already installed and connected here. Verify "
        "its claims with safe read-only commands (auth status, tools list, memory "
        "recent); review install and `forgetful serve` instructions by inspection only — "
        "do not run installers or start servers."
    ),
    "forgetful-context-gather": (
        "Note for this skill: invoke the workflow for a realistic request of your own "
        "choosing — search the knowledge base first and pick a topic it actually covers."
    ),
    "forgetful-encode-repo": (
        "Note for this skill: encode the repository at ./fixture-repo in your working "
        "directory."
    ),
}


def build_prompt(skill: str) -> str:
    note = _PER_SKILL_NOTES.get(skill, "")
    if note:
        note = f"\n{note}\n"
    return _TEMPLATE.format(note=note, contract=report_contract_example())

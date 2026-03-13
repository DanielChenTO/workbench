import assert from 'node:assert/strict'

import {
  buildDefaultCodeChangeStages,
  buildLocalAutopilotPrompt,
  composeSharedOutcomeContract,
} from './shared-outcome-contract.ts'

const contract = 'Goal\nTest shared helpers with a realistic contract.'

assert.equal(
  composeSharedOutcomeContract(contract),
  `## Shared Outcome Contract\n\n${contract}`,
)

const stages = buildDefaultCodeChangeStages(contract)
assert.equal(stages.length, 4)
assert.equal(stages[0].name, 'explore')
assert.equal(stages[0].autonomy, 'research')
assert.equal(stages[1].name, 'implement')
assert.equal(stages[1].autonomy, 'local')
assert.equal(stages[2].name, 'validate')
assert.equal(stages[2].autonomy, 'research')
assert.equal(stages[2].review_gate, true)
assert.equal(stages[2].reject_action, 'fail')
assert.equal(stages[3].name, 'review')
assert.equal(stages[3].autonomy, 'research')
assert.equal(stages[3].review_gate, true)
assert.equal(stages[3].loop_to, 1)

for (const stage of stages) {
  assert.equal(stage.prompt.includes('## Shared Outcome Contract\n\n'), true)
  assert.equal(stage.prompt.includes(contract), true)
}

const goal = 'Continue work safely'

assert.equal(
  buildLocalAutopilotPrompt(goal, 'full'),
  [
    '# Local Autopilot Orchestrator',
    '',
    'You are a local-only orchestrator. Keep making progress without asking for more input unless truly blocked.',
    '',
    'Rules:',
    '- Only local-safe work is allowed.',
    '- Never push or create PRs.',
    "- Use workbench todos tagged with 'autopilot' as the primary backlog.",
    '- Mirror todo state into work-directory/backlog.md so humans can inspect the queue easily.',
    '- Use the backlog, todos, and recent log entries to choose the next task.',
    '- For normal code changes, use the default workflow: explore -> implement -> validate -> review.',
    '- Failed validation requires a human-review report and the item should be parked in a review queue.',
    '- Continue to the next local-safe item until no such item remains.',
    '',
    'Goal:',
    goal,
  ].join('\n'),
)

assert.equal(
  buildLocalAutopilotPrompt(goal, 'compact'),
  [
    '# Local Autopilot Orchestrator',
    '',
    'Continue local-safe work until blocked.',
    'Never push or create PRs.',
    'Use backlog and logs to choose the next item.',
    '',
    'Goal:',
    goal,
  ].join('\n'),
)

console.log('ok')

import assert from 'node:assert/strict'

import {
  addUnique,
  backlogFromTodos,
  normalizeItem,
  parseBacklog,
  removeMatching,
  titleOf,
} from './manage-autopilot-backlog.ts'

const parsed = parseBacklog(`# Local Backlog

## Active

- [ ] Task A

## Review Queue

- [ ] Task B — Failed validation

## Notes

- Keep going
`)

assert.deepEqual(parsed.active, ['Task A'])
assert.deepEqual(parsed.review, ['Task B — Failed validation'])
assert.deepEqual(parsed.notes, ['- Keep going'])

assert.equal(normalizeItem('Task A'), 'Task A')
assert.equal(normalizeItem('Task A', 'Needs tests'), 'Task A — Needs tests')
assert.equal(titleOf('Task A — Needs tests'), 'Task A')

assert.deepEqual(addUnique(['Task A'], 'Task A — Updated'), ['Task A — Updated'])
assert.deepEqual(removeMatching(['Task A', 'Task B'], 'Task A'), ['Task B'])

const backlog = backlogFromTodos(
  [
    { id: '1', title: 'Build helper', description: 'Add tests', status: 'todo', tags: ['autopilot'] },
    { id: '2', title: 'Validation fail', description: 'Missing evidence', status: 'review', tags: ['autopilot', 'review_queue'] },
    { id: '3', title: 'Ignore me', status: 'todo', tags: ['manual'] },
  ],
  ['- note'],
)

assert.deepEqual(backlog.active, ['Build helper — Add tests'])
assert.deepEqual(backlog.review, ['Validation fail — Missing evidence'])
assert.deepEqual(backlog.notes, ['- note'])

console.log('ok')

export const UX_TEST_PATHS = [
  {
    id: 'board-to-desk',
    title: 'Board to desk handoff',
    goal: 'Confirm the user can narrow candidates without losing context.',
    startRoute: '/watchlist',
    startLabel: 'Start on watchlist',
    passCriteria: [
      'A board leader can move from Watchlist to Compare without retyping symbols or losing timeframe context.',
      'The desk explains why the ticker arrived there and offers a clear return path.',
      'The user can tell whether the next move is promote, review, or stand down without scanning unrelated panels.',
    ],
    watchFor: [
      'Users hesitate between Compare and the desk because the roles feel too similar.',
      'The arrival banner is noticed but not trusted.',
      'The next action is visible, but not obviously safer than the risky one.',
    ],
  },
  {
    id: 'alert-to-risk-review',
    title: 'Alert to live-risk review',
    goal: 'Confirm interruptions move into the right action surface instead of creating noise.',
    startRoute: '/alerts',
    startLabel: 'Start on alerts',
    passCriteria: [
      'A high-priority alert sends the user toward Compare, Trades, or the desk with a clear reason.',
      'Users can tell whether the alert is a triage item or a route blocker.',
      'After checking the alert, the user can return to the main workflow without losing orientation.',
    ],
    watchFor: [
      'Alert copy feels urgent, but does not explain what surface to open next.',
      'Users bounce between Alerts and Trades because both appear to own live risk.',
      'The workflow strip does not give enough recovery context after the interruption.',
    ],
  },
  {
    id: 'portfolio-review-loop',
    title: 'Portfolio to review loop',
    goal: 'Confirm replay evidence leads cleanly into repair work and back to the desk.',
    startRoute: '/portfolio',
    startLabel: 'Start on portfolio',
    passCriteria: [
      'Users can open a replayed ticker on the desk and understand where it came from.',
      'Users can move from replay evidence into Journal or Notes without the repair thread resetting.',
      'A cleared repair can make its way back to the desk with the original context intact.',
    ],
    watchFor: [
      'Replay rows are informative but do not make the next action obvious.',
      'Journal and Notes feel like separate tools instead of one repair loop.',
      'Users dismiss context banners because they feel repetitive or stale.',
    ],
  },
]

export const UX_TEST_COUNTS = {
  walkthroughs: UX_TEST_PATHS.length,
  criteria: UX_TEST_PATHS.reduce((count, path) => count + path.passCriteria.length, 0),
}

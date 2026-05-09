import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(__dirname, '..')

function readProjectFile(path) {
  return readFileSync(resolve(frontendRoot, path), 'utf8')
}

const appShell = readProjectFile('src/components/AppShell.jsx')
assert.doesNotMatch(appShell, /FocusApertureFrame/, 'normal shell should not wrap authenticated pages in Focus Aperture')
assert.doesNotMatch(appShell, /DecisionRibbon/, 'normal shell should not render the Decision Ribbon')
assert.doesNotMatch(appShell, /focus-aperture/, 'normal shell should not render Focus Aperture rails')
assert.doesNotMatch(appShell, /app-shell--focus-/, 'normal shell should not add focus-mode shell classes')
assert.match(appShell, /\{children\}/, 'normal shell should render route children directly')
assert.match(appShell, /ui-shell__body/, 'normal shell body should remain the primary page container')

const appSource = readProjectFile('src/App.jsx')
assert.match(appSource, /showWorkflowStatusStrip === false \? null : <WorkflowStatusStrip \/>/)
assert.doesNotMatch(appSource, /visualFocusMode !== 'full_console'/, 'workflow strip should no longer be gated by Focus Aperture mode')

const focusFrame = readProjectFile('src/components/focus/FocusApertureFrame.jsx')
assert.match(focusFrame, /export default function FocusApertureFrame/, 'dormant focus component can stay available for future experiments')

console.log('focus-aperture-smoke passed: normal shell restored')

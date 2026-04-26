import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import crypto from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { chromium } from 'playwright'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const snapshotDir = path.join(__dirname, '__snapshots__')
const artifactDir = path.join(__dirname, '__artifacts__')
const baselinePath = path.join(snapshotDir, 'chart-demo.baseline.png')
const baselineScenePath = path.join(snapshotDir, 'chart-demo.scene.json')
const currentPath = path.join(artifactDir, 'chart-demo.current.png')
const metadataPath = path.join(artifactDir, 'chart-demo.metrics.json')
const port = 4174
const origin = `http://127.0.0.1:${port}`
const pageUrl = `${origin}/chart-demo.html`
const updateSnapshot = process.argv.includes('--update') || process.env.UPDATE_CHART_BROWSER_SNAPSHOT === '1'

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForServer(url, timeoutMs = 30000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // retry
    }
    await sleep(250)
  }
  throw new Error(`Timed out waiting for dev server at ${url}`)
}

function stopServer(serverProcess) {
  if (!serverProcess || serverProcess.exitCode !== null) return Promise.resolve()
  return new Promise((resolve) => {
    if (process.platform === 'win32') {
      const killer = spawn('taskkill', ['/pid', String(serverProcess.pid), '/T', '/F'], { stdio: 'ignore' })
      killer.on('exit', () => resolve())
      killer.on('error', () => resolve())
      return
    }
    serverProcess.kill('SIGTERM')
    serverProcess.on('exit', () => resolve())
  })
}

function writeMetrics(payload) {
  fs.mkdirSync(artifactDir, { recursive: true })
  fs.writeFileSync(metadataPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function stableSceneSnapshot(scene) {
  return {
    sceneHash: scene.sceneHash,
    rootBox: scene.rootBox,
    svgBox: scene.svgBox,
    nodeCounts: scene.nodeCounts,
    title: scene.title,
  }
}

async function main() {
  fs.mkdirSync(snapshotDir, { recursive: true })
  fs.mkdirSync(artifactDir, { recursive: true })

  const serverProcess =
    process.platform === 'win32'
      ? spawn(
          'cmd.exe',
          ['/d', '/s', '/c', `npm.cmd run dev -- --host 127.0.0.1 --port ${port} --strictPort`],
          {
            cwd: frontendDir,
            stdio: ['ignore', 'pipe', 'pipe'],
            windowsHide: true,
          },
        )
      : spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
          cwd: frontendDir,
          stdio: ['ignore', 'pipe', 'pipe'],
        })

  let browser

  try {
    await waitForServer(pageUrl)

    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1320 },
      deviceScaleFactor: 1,
      colorScheme: 'dark',
    })

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(pageUrl, { waitUntil: 'networkidle' })
    await page.waitForFunction(() => document.fonts?.status === 'loaded' || !document.fonts)
    await page.locator('[data-sale-chart-root]').waitFor({ state: 'visible' })
    await page.locator('.fresh-chart svg').waitFor({ state: 'visible' })
    await page.waitForTimeout(300)

    const chartRoot = page.locator('[data-sale-chart-root]')
    await chartRoot.screenshot({ path: currentPath })
    const browserScene = await page.evaluate(() => {
      const root = document.querySelector('[data-sale-chart-root]')
      const svg = root?.querySelector('.fresh-chart svg')
      const rootBox = root?.getBoundingClientRect()
      const svgBox = svg?.getBoundingClientRect()
      const svgMarkup = svg?.outerHTML?.replace(/\s+/g, ' ').trim() || ''

      return {
        title: document.querySelector('h1')?.textContent?.trim() || '',
        rootBox: rootBox
          ? {
              width: Number(rootBox.width.toFixed(2)),
              height: Number(rootBox.height.toFixed(2)),
            }
          : null,
        svgBox: svgBox
          ? {
              width: Number(svgBox.width.toFixed(2)),
              height: Number(svgBox.height.toFixed(2)),
            }
          : null,
        nodeCounts: svg
          ? {
              paths: svg.querySelectorAll('path').length,
              rects: svg.querySelectorAll('rect').length,
              lines: svg.querySelectorAll('line').length,
              texts: svg.querySelectorAll('text').length,
              circles: svg.querySelectorAll('circle').length,
            }
          : null,
        svgMarkup,
      }
    })

    const scene = {
      ...browserScene,
      sceneHash: sha256(browserScene.svgMarkup),
    }

    if (updateSnapshot || !fs.existsSync(baselinePath)) {
      fs.copyFileSync(currentPath, baselinePath)
      fs.writeFileSync(baselineScenePath, `${JSON.stringify(stableSceneSnapshot(scene), null, 2)}\n`, 'utf8')
      writeMetrics({
        mode: 'baseline-updated',
        baselinePath,
        baselineScenePath,
        currentPath,
        viewport: { width: 1600, height: 1320 },
        scene: stableSceneSnapshot(scene),
      })
      console.log('chart browser regression baseline updated')
      return
    }

    const baselineScene = JSON.parse(fs.readFileSync(baselineScenePath, 'utf8'))

    const metrics = {
      mode: 'comparison',
      baselinePath,
      baselineScenePath,
      currentPath,
      scene: stableSceneSnapshot(scene),
    }
    writeMetrics(metrics)

    if (JSON.stringify(stableSceneSnapshot(scene)) !== JSON.stringify(baselineScene)) {
      throw new Error(
        `Browser scene regression changed. Baseline: ${baselineScenePath}. Current metrics: ${metadataPath}`,
      )
    }

    console.log(JSON.stringify(metrics, null, 2))
  } finally {
    if (browser) await browser.close()
    await stopServer(serverProcess)
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
})

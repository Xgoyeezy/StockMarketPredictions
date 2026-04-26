import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build, mergeConfig } from 'vite'
import baseConfig from '../vite.config.js'

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))

const exampleBuilds = [
  {
    name: 'chart-demo',
    input: resolve(frontendRoot, 'chart-demo.html'),
    outDir: resolve(frontendRoot, 'dist/examples/chart-demo'),
  },
  {
    name: 'chart-embed',
    input: resolve(frontendRoot, 'chart-embed.html'),
    outDir: resolve(frontendRoot, 'dist/examples/chart-embed'),
  },
]

for (const example of exampleBuilds) {
  process.stdout.write(`\n[chart-example] building ${example.name}\n`)
  await build(
    mergeConfig(baseConfig, {
      build: {
        outDir: example.outDir,
        emptyOutDir: true,
        rollupOptions: {
          input: example.input,
        },
      },
    }),
  )
}

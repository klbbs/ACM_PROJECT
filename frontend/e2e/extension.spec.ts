import { test, expect, chromium } from '@playwright/test'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

test('downloaded Chromium extension reads a synthetic HttpOnly cookie using real extension APIs', async ({
  request,
}) => {
  const directory = mkdtempSync(join(tmpdir(), 'acm-extension-e2e-'))
  const extension = join(directory, 'extension')
  let context
  try {
    const response = await request.get('/api/browser-extension/chromium.zip')
    expect(response.ok()).toBeTruthy()
    const archive = join(directory, 'extension.zip')
    writeFileSync(archive, await response.body())
    execFileSync(process.env.ACM_TEST_PYTHON || 'python3', [
      '-c',
      'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',
      archive,
      extension,
    ])
    const manifestPath = join(extension, 'manifest.json')
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    // Test-only pregrant: Chromium's native permission dialog isn't a web page.
    // The shipped manifest keeps this optional; denial/gesture paths have unit coverage.
    manifest.host_permissions = ['https://codeforces.com/*']
    writeFileSync(manifestPath, JSON.stringify(manifest))
    context = await chromium.launchPersistentContext(join(directory, 'profile'), {
      channel: 'chromium',
      headless: true,
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    })
    await context.route('https://codeforces.com/**', (route) =>
      route.fulfill({ contentType: 'text/html', body: '<h1>Synthetic platform fixture</h1>' }),
    )
    await context.addCookies([
      {
        name: 'test_session',
        value: 'synthetic-http-only',
        url: 'https://codeforces.com',
        httpOnly: true,
        secure: true,
        sameSite: 'Lax',
      },
    ])
    const official = await context.newPage()
    await official.goto('https://codeforces.com/profile/synthetic')
    const popup = await context.newPage()
    await popup.addInitScript(() => {
      // Opening popup.html as a test tab differs from clicking the toolbar action.
      // Select the platform tab while retaining real permissions/cookies/clipboard APIs.
      const api = (globalThis as any).chrome
      const query = api.tabs.query.bind(api.tabs)
      api.tabs.query = () => query({ url: 'https://codeforces.com/*' })
    })
    const digest = createHash('sha256').update(extension).digest('hex').slice(0, 32)
    const extensionId = [...digest]
      .map((char) => String.fromCharCode(97 + parseInt(char, 16)))
      .join('')
    await popup.goto(`chrome-extension://${extensionId}/popup.html`)
    await expect(popup.locator('#platform')).toContainText('Codeforces')
    await popup.getByRole('button', { name: '获取 Cookie 并复制' }).click()
    await expect(popup.getByRole('status')).toContainText('已复制 1 项 Cookie')
    await expect(popup.locator('#value')).toHaveValue('')
    await expect(popup.locator('#fallback')).toBeHidden()
  } finally {
    await context?.close()
    rmSync(directory, { recursive: true, force: true })
  }
})

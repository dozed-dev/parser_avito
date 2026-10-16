from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    context = playwright.chromium.launch_persistent_context(
        "/app/storage/container-chromium-profile",
        headless=False,
        executable_path="/ms-playwright/chromium-1169/chrome-linux/chrome",
        viewport=None,
        args=[
            "--no-sandbox",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--crash-dumps-dir=/tmp",
            "--ozone-platform=wayland",
        ],
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.avito.ru", wait_until="domcontentloaded", timeout=0)
    print("Chromium started:", page.title(), flush=True)
    input("Press Enter to close Chromium... ")
    context.close()

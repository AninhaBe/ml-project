"""Testa scraping do ML com cookies do perfil Chrome da Ana Livia."""
import asyncio, json, os, shutil, tempfile
from pathlib import Path
from playwright.async_api import async_playwright

CHROME_PROFILE = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data/Profile 9"
COOKIES_DB = CHROME_PROFILE / "Network/Cookies"

async def main():
    async with async_playwright() as p:
        # Usa o Chrome do sistema com o perfil da Ana Livia diretamente
        # Copia o perfil para temp para evitar lock
        tmp = Path("G:/ml_market_agent/_chrome_profile_tmp")
        print(f"Copiando perfil para {tmp}...")
        shutil.copytree(CHROME_PROFILE, tmp, ignore=shutil.ignore_patterns(
            "*.log", "*.lck", "Lock", "LOCK", "SingletonLock"
        ))
        print("Perfil copiado.")

        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(tmp),
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        print("Abrindo ML...")
        await page.goto("https://www.mercadolivre.com.br/p/MLB63230073", wait_until="networkidle", timeout=30000)
        print("URL final:", page.url)

        # Checa se está logado
        title = await page.title()
        print("Título:", title[:60])

        # Procura dados de vendas no HTML
        content = await page.content()
        import re
        for pattern in [r'\+(\d+)\s*vendidos', r'"sold_quantity":(\d+)', r'"units_sold":(\d+)']:
            m = re.search(pattern, content, re.IGNORECASE)
            if m:
                print(f"ACHOU {pattern}: {m.group(0)}")

        # Tira screenshot para ver o que carregou
        await page.screenshot(path="g:/ml_market_agent/_playwright_test.png")
        print("Screenshot salvo em _playwright_test.png")

        await browser.close()
        shutil.rmtree(tmp, ignore_errors=True)

asyncio.run(main())

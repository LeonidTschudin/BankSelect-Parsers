import asyncio
import random
import collections  # Импортируем deque
import re
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import requests
import warnings

from playwright.async_api import async_playwright, ViewportSize, BrowserContext, Playwright, Browser, Page
from urllib3.exceptions import InsecureRequestWarning
from datetime import datetime

warnings.simplefilter(action='ignore', category=InsecureRequestWarning)


class StopProcessingURL(Exception):
    pass


class URL:
    def __init__(self, url: str, response_code: int | None = None, referrers: tuple[str, ...] = tuple()):
        self._referrers = referrers
        self._url_parsed = urlparse(url.strip())  # Нормализуем URL сразу
        self._url_string = self._url_parsed.geturl()  # Кэшируем строку URL
        self._response_code = response_code

    @property
    def data(self) -> tuple[str, int | None, tuple[str, ...]]:
        return self.url, self.response, self.referrers

    @property
    def domain(self) -> str:
        return self._url_parsed.netloc

    @property
    def url(self) -> str:
        return self._url_string  # Возвращаем кэшированную строку

    @property
    def response(self) -> int | None:
        return self._response_code

    @property
    def referrers(self) -> tuple[str, ...]:
        return self._referrers

    def set_response(self, response: int) -> None:
        self._response_code = response

    def __str__(self):
        return self.url

    # Важно для работы с set
    def __eq__(self, other):
        if isinstance(other, URL):
            return self.url == other.url
        return False

    def __hash__(self):
        return hash((self.url, self.referrers))


class DomainScanner:
    MAX_DEPTH = 10
    DELAY = 0.1  # Небольшая задержка между перед следующим запросом
    TIMEOUT = 8_000  # timeout в мс
    VERIFY_REQUESTS = False
    BASE_HEADERS = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    }

    PAGE_INIT_SCRIPT = """
            // Удаление webdriver свойства - сайты проверяют это чтобы обнаружить ботов
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,  // Всегда возвращает undefined
            });

            // Переопределение chrome runtime чтобы скрыть автоматизацию
            window.chrome = {
                runtime: {},  // Пустой объект
            };
"""
    BROWSER_ARGS = [
        '--no-sandbox',  # Отключает sandbox для Docker
        '--disable-blink-features=AutomationControlled',  # Скрывает автоматизацию
        '--blink-settings=imagesEnabled=false',  # Отключает загрузку изображений
        '--disable-gpu',  # Полностью отключает GPU-ускорение.
        '--disable-extensions',  # Отключает все расширения браузера.
        '--disable-plugins',  # Отключает плагины (Flash, PDF-просмотрщик и т.д.)
        '--aggressive-cache-discard',  # Агрессивно очищает кэш для экономии памяти
        '--disable-dev-shm-usage',  # Для Linux систем
    ]

    def __init__(self, start_url: str) -> None:
        # Инициализация start_url_obj с referrers = (start_url,)
        self.start_url_obj = URL(start_url, response_code=None, referrers=(start_url,))
        self.base_domain = self.start_url_obj.domain

        self.playwright: Playwright | None = None
        self.browser: Browser | None = None

        self.ua = UserAgent(platforms="desktop")  # Фейковый UserAgent

        # Используем set для быстрой проверки уже посещенных URL
        self.visited_urls: set[str] = set()
        self.visited_url_objs: set[URL] = set()  # Для сохранения полного объекта URL

        # Используем deque для обхода в ширину (BFS)
        self.urls_to_visit: collections.deque[URL] = collections.deque()
        self.urls_to_visit.append(self.start_url_obj)

        self.scanned_count = 0  # Счетчик для вывода

        self.semaphore: str = ""  # Тут будут записаны места для процессов 1 - занят; 0 - свободен

    async def start_browser(self, headless: bool = True):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=self.BROWSER_ARGS  # Аргументы командной строки для браузера
        )

    async def _get_context(self) -> BrowserContext:
        return await self.browser.new_context(
            viewport=ViewportSize({
                'width': random.randint(1080, 1920),
                'height': random.randint(1080, 1920), }),
            user_agent=self.ua.random,
            locale='ru-RU',
            timezone_id='Europe/Moscow',
            extra_http_headers=self.BASE_HEADERS
        )

    def _save_data(self):
        """Сохраняет собранные данные в файл."""
        # Создаем директорию, если она не существует
        import os
        output_dir = f"data/crawled"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        filename = f"{self.base_domain.replace('.', '_')}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as file_out:
            # Сортируем по URL для предсказуемого порядка
            sorted_urls = sorted(self.visited_urls)
            for url in sorted_urls:
                # Записываем данные в формате, который может быть легко прочитан
                file_out.write(f"{url}\n")
        print(f"Данные сохранены в {filepath}")

    def _is_valid_url(self, url_str: str, current_url_obj: URL) -> bool:
        """Проверяет, является ли URL подходящим для дальнейшего сканирования."""
        if (
                url_str.startswith("http") and
                len(current_url_obj.referrers) <= self.MAX_DEPTH and  # Проверка глубины парсинга сайта
                urlparse(url_str).netloc == self.base_domain and  # Проверка домена
                not any(char in url_str for char in ["%", "#", "&", "?", "@", "="]) and  # Исключаем параметры, якоря
                url_str not in self.visited_urls
        ):
            return True
        return False

    def _parse_page_content(self, content: str, request_url: str, referrers: tuple, response_url: str) -> None:
        def is_url_file(url: str) -> bool:
            """Проверят, ведёт ли ссылка на файл, ищя расширения фалов в конце"""
            url = urlparse(url).path.rstrip("/")
            has_extension = bool(re.search(r'\.[a-zA-Z0-9]{2,6}$', url))
            return has_extension

        # Парсим HTML, используя response.content для потенциальной экономии памяти
        soup = BeautifulSoup(content, 'html.parser')

        for link_tag in soup.find_all('a', href=True):
            href = link_tag['href']
            absolute_url = urljoin(request_url, href)  # Получаем абсолютный URL

            # Формируем новые referrers
            new_referrers = referrers
            if response_url not in referrers:
                new_referrers = referrers + (request_url,)

            next_url_obj = URL(absolute_url, referrers=new_referrers)

            if self._is_valid_url(next_url_obj.url, next_url_obj):
                if is_url_file(absolute_url):
                    self.visited_urls.add(absolute_url)
                    self._print_progress(absolute_url)
                    continue

                self.urls_to_visit.append(next_url_obj)
            else:
                continue

    async def _new_page(self) -> tuple[Page, BrowserContext]:
        context = await self._get_context()
        page = await context.new_page()
        await page.add_init_script(self.PAGE_INIT_SCRIPT)

        return page, context

    async def _process_url(self, request_url_obj: URL) -> None:
        """Обрабатывает один URL: делает запрос, парсит, добавляет ссылки."""
        async def stop_processing() -> None:
            self.semaphore = self.semaphore.replace("1", "0", 1)
            await page.close()
            await context.close()

        self.semaphore = self.semaphore.replace("0", "1", 1)

        request_url, referrers = request_url_obj.url, request_url_obj.referrers

        page, context = await self._new_page()

        try:
            try:
                response = await page.goto(request_url, wait_until="load", timeout=self.TIMEOUT)
                # await page.wait_for_selector("xpath=//a[@href]", timeout=self.TIMEOUT)
                # await page.wait_for_timeout(timeout=self.TIMEOUT)

            except Exception as ex_:
                print(ex_.__class__.__name__)

            finally:
                await asyncio.sleep(self.DELAY)

            content = await page.inner_html("html")  # Получаем только HTML без лишних данных
            print(content + "\n\n\n", file=open("exception.html", "a", encoding="utf-8"))
            response_url = page.url.strip()
            response_code = response.status

            await page.close()  # Можно закрыть страницу

            # Создаем новый URL-объект с учетом ответа сервера
            processed_url_obj = URL(response_url, response_code, referrers=referrers)

            self.visited_urls.add(response_url.rstrip("\\/"))  # Добавляем URL после редиректа
            self.visited_url_objs.add(processed_url_obj)  # Сохраняем полный объект

            self._print_progress(request_url, response_code)

            # Если домен отличается или мы наткнулись на файл, прекращаем обработку этой ветки
            if processed_url_obj.domain != self.base_domain or "." in response_url.split("/")[-1]:
                raise StopProcessingURL

            self._parse_page_content(content, request_url, referrers, response_url)
            raise StopProcessingURL

        except StopProcessingURL:
            pass

        except requests.exceptions.RequestException as ex_:
            # Обработка ошибок запросов
            print(f"Ошибка запроса {request_url}: {ex_}")
            self.visited_urls.add(request_url)

        except Exception as ex_:
            print(f"Неизвестная ошибка при обработке {request_url}: {ex_}")
            self.visited_urls.add(request_url)  # Добавляем в посещённые, чтобы не повторять

        finally:
            await stop_processing()

    async def start(self, max_concurrent_tabs):
        try:
            print(f"Начинаем сканирование с URL: {self.start_url_obj.url} на домене: {self.base_domain}")
            self.semaphore = "0" * max_concurrent_tabs
            await self.start_browser(False)

            async with asyncio.TaskGroup() as task_group:
                while self.urls_to_visit or "1" in self.semaphore:  # Пока есть непосещённые url или активные процессы парсинга
                    if "0" in self.semaphore:  # Если есть свободный процесс

                        if self.urls_to_visit:  # Если есть что парсить
                            current_url_obj = self.urls_to_visit.popleft()
                            url = current_url_obj.url

                            if url not in self.visited_urls:
                                self.visited_urls.add(url.rstrip("\\/"))
                                task_group.create_task(self._process_url(current_url_obj))

                    await asyncio.sleep(0.01)

            print("\nСканирование завершено.")
            print(self.urls_to_visit)

        except KeyboardInterrupt:
            print("\nСканирование прервано пользователем.")

        finally:
            self._save_data()

    def _print_progress(self, url, response_code=-1):
        self.scanned_count += 1
        print(f"{self.scanned_count}) Посещаем {url} | Status:{response_code}")


async def main():
    ds = DomainScanner("https://www.vtb.ru/")
    await ds.start(1)


if __name__ == '__main__':
    start_time = datetime.now()
    print(start_time)
    asyncio.run(main())
    delta = datetime.now() - start_time
    print(delta)

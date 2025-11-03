import time
import collections  # Импортируем deque
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import requests
import sys
import warnings

from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter(action='ignore', category=InsecureRequestWarning)


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
    DELAY = 0.1  # Небольшая задержка
    TIMEOUT = 10
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

    def __init__(self, start_url: str) -> None:
        # Инициализация start_url_obj с referrers = (start_url,)
        self.start_url_obj = URL(start_url, response_code=None, referrers=(start_url,))
        self.base_domain = self.start_url_obj.domain

        self.ua = UserAgent(platforms="desktop")  # Фейковый UserAgent

        self.session = requests.Session()
        self.session.headers.update(self.BASE_HEADERS)

        # Используем set для быстрой проверки уже посещенных URL
        self.visited_urls: set[str] = set()
        self.visited_url_objs: set[URL] = set()  # Для сохранения полного объекта URL

        # Используем deque для обхода в ширину (BFS)
        self.urls_to_visit: collections.deque[URL] = collections.deque()
        self.urls_to_visit.append(self.start_url_obj)

        self.scanned_count = 0  # Счетчик для вывода

    def _is_valid_url(self, url_str: str, current_url_obj: URL) -> bool:
        """Проверяет, является ли URL подходящим для дальнейшего сканирования."""
        if (
                url_str.startswith("http") and
                len(current_url_obj.referrers) <= self.MAX_DEPTH and  # Проверка глубины парсинга сайта
                urlparse(url_str).netloc == self.base_domain and  # Проверка домена
                not any(char in url_str for char in ["%", "#", "&", "?"]) and  # Исключаем параметры, якоря
                url_str not in self.visited_urls
        ):
            return True
        return False

    def _process_url(self, request_url_obj: URL) -> None:
        """Обрабатывает один URL: делает запрос, парсит, добавляет ссылки."""
        request_url = request_url_obj.url
        referrers = request_url_obj.referrers

        if request_url in self.visited_urls:
            return

        if "." in request_url.split("/")[-1]:
            self.visited_urls.add(request_url)
            self.visited_url_objs.add(request_url_obj)

            self.scanned_count += 1
            print(
                f"{self.scanned_count}) Посещаем {request_url}"
            )
            return

        if request_url in self.visited_urls:
            return

        self._prepare_for_request()
        time.sleep(self.DELAY)

        try:
            response = self.session.get(request_url,
                                        timeout=self.TIMEOUT,
                                        allow_redirects=True,
                                        verify=self.VERIFY_REQUESTS)
            response.raise_for_status()  # Выбрасывает исключение для плохих ответов (4xx, 5xx)

            response_url = response.url.strip()
            response_code = response.status_code

            # Создаем новый URL-объект с учетом ответа сервера
            processed_url_obj = URL(response_url, response_code, referrers=referrers)

            self.visited_urls.add(request_url)  # Добавляем исходный URL в посещенные
            self.visited_urls.add(response_url)  # Добавляем URL после редиректа

            self.visited_url_objs.add(processed_url_obj)  # Сохраняем полный объект

            self.scanned_count += 1
            print(
                f"{self.scanned_count}) Посещаем {request_url} | "
                f"status:{response_code} | "
                # f"ref: {', '.join(referrers)} | -> {response_url}"
            )

            # Если домен отличается или мы наткнулись на файл, прекращаем обработку этой ветки
            if processed_url_obj.domain != self.base_domain or "." in response_url.split("/")[-1]:
                return

            # Парсим HTML, используя response.content для потенциальной экономии памяти
            soup = BeautifulSoup(response.content, 'html.parser')

            for link_tag in soup.find_all('a', href=True):
                href = link_tag['href']
                absolute_url = urljoin(request_url, href)  # Получаем абсолютный URL

                # Формируем новые referrers
                new_referrers = referrers
                if response_url not in referrers:
                    new_referrers = referrers + (request_url,)

                next_url_obj = URL(absolute_url, referrers=new_referrers)

                if self._is_valid_url(next_url_obj.url, next_url_obj):
                    self.urls_to_visit.append(next_url_obj)

        except requests.exceptions.RequestException as e:
            # Обработка ошибок запросов
            print(f"Ошибка запроса {request_url}: {e}")
            self.visited_urls.add(request_url)

        except KeyboardInterrupt:
            self._save_data()
            print("\nСканирование прервано пользователем.")
            sys.exit(0)

        except Exception as ex_:
            print(f"Неизвестная ошибка при обработке {request_url}: {ex_}")
            self.visited_urls.add(request_url)  # Добавляем в посещённые, чтобы не повторять

    def _prepare_for_request(self) -> None:
        """Обновляет User-Agent для следующего запроса."""
        new_ua_header = {'User-Agent': self.ua.random}
        self.session.headers.update(new_ua_header)

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
            sorted_urls = sorted(self.visited_url_objs, key=lambda url_obj: url_obj.url)
            for url_obj in sorted_urls:
                # Записываем данные в формате, который может быть легко прочитан
                file_out.write(f"{url_obj.url}, {url_obj.response}, {url_obj.referrers}\n")
        print(f"Данные сохранены в {filepath}")

    def start(self) -> None:
        """Запускает процесс сканирования."""
        print(f"Начинаем сканирование с URL: {self.start_url_obj.url} на домене: {self.base_domain}")
        while self.urls_to_visit:
            try:
                current_url_obj = self.urls_to_visit.popleft()  # Извлекаем из начала очереди (BFS)
                self._process_url(current_url_obj)
            except KeyboardInterrupt:
                self._save_data()
                print("\nСканирование прервано пользователем.")
                sys.exit(0)
            except Exception as e:
                print(f"Ошибка в основном цикле: {e}")

        print("\nСканирование завершено.")
        self._save_data()


if __name__ == '__main__':
    ds = DomainScanner("https://www.vtb.ru/")
    ds.start()

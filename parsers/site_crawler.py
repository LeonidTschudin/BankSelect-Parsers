import time
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import requests
import sys

sys.setrecursionlimit(9999999)


class URL:
    def __init__(self, url: str, response_code: int | None, referrers: tuple[str] = ("",)):
        self._referrers = referrers
        self._url = urlparse(url)
        self._response_code = response_code

    @property
    def data(self) -> tuple[str, int, tuple[str]]:
        return self._url.geturl(), self._response_code, self._referrers

    @property
    def domain(self) -> str:
        return self._url.netloc

    @property
    def url(self) -> str:
        return self._url.geturl().strip()

    @property
    def response(self) -> int:
        return self._response_code

    @property
    def referrers(self) -> tuple[str]:
        return self._referrers

    def set_response(self, response: int) -> None:
        self._response_code = response

    def __str__(self):
        return self._url.geturl()


class DomainScanner:
    MAX_DEPTH = 999
    DELAY = 0
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
        self.start_url_obj = URL(start_url, None, (start_url,))
        self.base_domain = self.start_url_obj.domain

        self.ua = UserAgent(platforms="desktop")

        self.session = requests.Session()
        self.session.headers.update(self.BASE_HEADERS)

        self.visited_url_objs: set[URL] = set()
        self.visited_urls: set[str] = set()

        self.not_visited_url_objs: set[URL] | set = set()
        self.not_visited_url_objs.add(self.start_url_obj)

    @property
    def _not_visited_urls(self) -> set[str]:
        res = set()
        for url_obj in self.not_visited_url_objs:
            res.add(url_obj.url)

        return res

    def crawl(self,
              delay: int = DELAY,
              timeout: int = TIMEOUT,
              verify: bool = VERIFY_REQUESTS) -> None:
        request_url = ""

        try:
            request_url_obj = self.not_visited_url_objs.pop()
            request_url = request_url_obj.url

            if request_url in self.visited_urls:
                return

            self._prepare_for_request()
            time.sleep(delay)

            response = self.session.get(request_url,
                                        timeout=timeout,
                                        allow_redirects=True,
                                        verify=verify)
            response_url = response.url.strip()

            response_referrers: tuple = request_url_obj.referrers

            response_url_obj = URL(response_url, response.status_code, referrers=response_referrers)

            print(
                f"{len(self.visited_urls)}) Посещаем {request_url} | "
                f"status:{response.status_code} | "
                f"{response_referrers=}"
            )

            self.visited_url_objs.add(response_url_obj)

            self.visited_urls.add(response_url)
            self.visited_urls.add(request_url)

            soup = BeautifulSoup(response.text, 'html.parser')
            not_visited_urls: set[str] = self._not_visited_urls

            for link in soup.find_all('a', href=True):
                href = link['href']
                redirect_url = urljoin(request_url, href)

                referrers = response_referrers
                if request_url not in response_referrers:
                    referrers: tuple = response_referrers + (request_url,)
                url_object = URL(redirect_url, None, referrers=referrers)

                if self._is_valid_redirect_url(redirect_url, url_object, not_visited_urls):
                    self.not_visited_url_objs.add(url_object)

        except KeyboardInterrupt:
            self._save_data()
            sys.exit(0)

        except Exception as ex_:
            print(f"Ошибка при обработке {request_url}: {ex_}")

        finally:
            if self.not_visited_url_objs:
                self.crawl(delay, timeout, verify)

            else:
                self._save_data()

    def _is_valid_redirect_url(self, url: str, current_url_obj: URL, not_visited_urls: set[str]) -> bool:
        if (url not in self.visited_urls and
                url not in not_visited_urls and
                len(current_url_obj.referrers) <= self.MAX_DEPTH and
                urlparse(current_url_obj.referrers[-1]).netloc == self.base_domain and
                url.startswith("http") and
                not any(["%" in url, "#" in url, "&" in url, "?" in url])):
            return True
        return False

    def _prepare_for_request(self) -> None:
        new_ua_header = {'User-Agent': self.ua.random}
        self.session.headers.update(new_ua_header)

    def _save_data(self):
        with open(f"data/crawled/{self.base_domain.replace(".", "_")}.txt", "w", encoding="utf-8") as file_out:
            for url_obj in sorted(self.visited_url_objs, key=lambda url_obj_: url_obj_.url):
                file_out.write(f"{url_obj.data}\n")


if __name__ == '__main__':
    ds = DomainScanner("https://www.vtb.ru/")
    ds.crawl()

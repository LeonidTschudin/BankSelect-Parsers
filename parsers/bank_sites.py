import requests
from bs4 import BeautifulSoup


def find_links_to_mainfin():
    all_links = set()

    for i in range(1, 19):
        url = f"https://mainfin.ru/banki?page={i}"

        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        body = soup.find("tbody")

        links = body.find_all('a', href=lambda href: href and href.startswith('/bank') and href.count('/') == 2)
        for link in links:
            all_links.add(link['href'])

        for link in links:
            print(link['href'])
            print(link.get_text(strip=True))
        print("-" * 100)

    print(len(all_links))
    with open("data/all_links_to_mainfin.txt", "w", encoding="utf-8") as f:
        for link in sorted(all_links):
            print(link, file=f)


def find_bank_names():
    with open("data/all_links_to_mainfin.txt", "r", encoding="utf-8") as f, \
            open("data/all_bank_names.txt", "w", encoding="utf-8") as f_out:
        all_links = list(map(str.strip, f.readlines()))
        for link in all_links:
            response = requests.get(f"https://mainfin.ru/{link}")
            soup = BeautifulSoup(response.text, 'html.parser')
            containers = soup.find_all('div', attrs={'class': 'container'})
            for container in containers:
                about_bank = container.find('h1')
                if about_bank:
                    bank_name = about_bank.text
                    print(bank_name)
                    print(bank_name, file=f_out)


def find_bank_urls():
    with open("data/all_links_to_mainfin.txt", "r", encoding="utf-8") as f, \
            open("data/all_bank_sites.txt", "w", encoding="utf-8") as f_out:
        all_links = list(map(str.strip, f.readlines()))
        for link in all_links:
            response = requests.get(f"https://mainfin.ru/{link}")
            soup = BeautifulSoup(response.text, 'html.parser')
            containers = soup.find_all('div', attrs={'class': 'container'})
            for container in containers:
                about_bank = container.find('div', attrs={'class': 'row about-bank-table'})
                if about_bank:
                    bank_url = about_bank.find('a', target='_blank').attrs['href']
                    print(bank_url, file=f_out)

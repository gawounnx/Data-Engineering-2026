import requests
from bs4 import BeautifulSoup
import csv
from pathlib import Path

BASE_URL = "https://books.toscrape.com/catalogue/page-{}.html"

def fetch_books_from_page(page: int) -> list[dict[str, str]]:
    """
    특정 페이지에서 책 정보를 크롤링합니다.

    Args:
        page: 크롤링할 페이지 번호

    Returns:
        책 정보 리스트. 각 책은 제목, 가격, 별점, 재고 여부를 포함하는 딕셔너리 형태.
    """
    url = BASE_URL.format(page)  # 페이지 번호를 URL에 삽입
    response = requests.get(url)  # HTTP GET 요청
    response.raise_for_status()  # 요청 실패 시 예외 발생

    soup = BeautifulSoup(response.text, "html.parser")  # HTML 파싱
    books = []

    # 페이지 내 모든 책 정보 추출
    for book in soup.select(".product_pod"):
        title = book.h3.a["title"]  # 책 제목
        price = book.select_one(".price_color").text  # 가격
        stock = book.select_one(".availability").text.strip()  # 재고 여부
        rating = book.select_one(".star-rating")["class"][1]  # 별점 (클래스에서 추출)

        books.append({
            "title": title,
            "price": price,
            "rating": rating,
            "stock": stock,
        })

    return books

def scrape_all_books() -> list[dict[str, str]]:
    """
    모든 페이지를 순회하며 책 정보를 크롤링합니다.

    Returns:
        모든 책 정보를 포함한 리스트.
    """
    all_books = []
    page = 1

    while True:
        print(f"[INFO] {page}페이지 크롤링 중...")

        try:
            books = fetch_books_from_page(page)
        except requests.exceptions.HTTPError:
            print(f"[INFO] {page}페이지가 존재하지 않습니다. 크롤링 종료!")
            break

        all_books.extend(books)
        page += 1

    return all_books

def save_books_to_csv(books: list[dict[str, str]], output_path: Path) -> None:
    """
    책 정보를 CSV 파일로 저장합니다.

    Args:
        books: 책 정보 리스트
        output_path: 저장할 CSV 파일 경로
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 상위 디렉토리 생성
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "price", "rating", "stock"])
        writer.writeheader()  # 헤더 작성
        writer.writerows(books)  # 데이터 작성

    print(f"[완료] {len(books)}권의 책 정보를 {output_path}에 저장했습니다.")

def main() -> None:
    """
    크롤링 스크립트의 진입점. 책 정보를 크롤링하고 CSV로 저장합니다.
    """
    print("[INFO] 크롤링 시작...")
    books = scrape_all_books()  # 모든 책 정보 크롤링
    output_path = Path("data/books.csv") # 저장 경로 설정
    save_books_to_csv(books, output_path)  # CSV로 저장

if __name__ == "__main__":
    main()
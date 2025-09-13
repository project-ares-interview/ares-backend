# tool_code.py

from duckduckgo_search import DDGS

class GoogleSearch:
    """
    DuckDuckGo 검색 엔진을 사용하여 웹 검색을 수행하는 클래스입니다.
    API 키 없이 사용할 수 있어 편리합니다.
    """
    def __init__(self):
        self.client = DDGS()

    def search(self, queries: list[str], num_results: int = 3) -> str:
        """
        주어진 쿼리 목록에 대해 웹 검색을 수행하고 결과를 요약된 문자열로 반환합니다.
        
        Args:
            queries (list[str]): 검색할 쿼리(검색어)의 리스트.
            num_results (int): 각 쿼리당 가져올 결과의 수.

        Returns:
            str: 검색 결과를 종합한 문자열.
        """
        all_results_text = ""
        for query in queries:
            try:
                results = self.client.text(
                    keywords=query,
                    max_results=num_results
                )
                if results:
                    all_results_text += f"'{query}'에 대한 검색 결과:\n"
                    for i, result in enumerate(results, 1):
                        all_results_text += f"{i}. 제목: {result['title']}\n"
                        all_results_text += f"   내용: {result['body']}\n"
                        all_results_text += f"   링크: {result['href']}\n\n"
                else:
                    all_results_text += f"'{query}'에 대한 검색 결과를 찾을 수 없습니다.\n\n"
            except Exception as e:
                all_results_text += f"'{query}' 검색 중 오류 발생: {e}\n\n"
        
        return all_results_text

# final_interview_rag.py에서 'from tool_code import google_search'로 호출할 수 있도록
# 클래스의 인스턴스를 생성합니다.
google_search = GoogleSearch()
WEBARENA_TASKS = [
    {
        "id": "0",
        "intent": "Wikipediaで'Large language model'のページに行き、最初の段落の要約を取得してください。",
        "start_url": "https://ja.wikipedia.org/",
        "evaluation_type": "string_match",
        "reference": "Large language model"
    },
    {
        "id": "1",
        "intent": "Google検索で'Python'を検索し、公式サイトのURLを見つけてください。",
        "start_url": "https://www.google.com/",
        "evaluation_type": "url_match",
        "reference": "https://www.python.org/"
    },
    {
        "id": "2",
        "intent": "Example.comに行き、ページ内のリンクをクリックしてください。",
        "start_url": "https://example.com/",
        "evaluation_type": "action_check",
        "reference": "click"
    }
]

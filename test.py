import json
from urllib.parse import urlencode

def main(inputs: str) -> dict:
    if isinstance(inputs, str):
        try:
            data = json.loads(inputs)
        except:
            data = {}
    else:
        data = inputs

    k_query = data.get("keywords", "")
    base = data.get("base", "")

    # Use urlencode to safely encode the query parameters
    params = {
        "q": f"site:{base}{k_query}",
        "tbs": "qdr:h,sbd:1",
        "tbm": "nws"
    }

    query_string = urlencode(params)
    bquery = f"https://www.google.com/search?{query_string}"

    return {"bloom_query": bquery}

if __name__ == "__main__":
    print(main("\n\n\n\n  \n  \n  \n  \n\n\n{\n  \"base\": \"bloomberg.com/news/articles\",\n  \"keywords\": \" China OR \"Hong Kong\" OR AI\"\n}"))
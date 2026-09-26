"""
Scenario: pagination
A search-results pagination helper with 2 planted bugs.

Run locally:
    flask --app app run
"""

from flask import Flask, request, jsonify

app = Flask(__name__)

ITEMS = [{"id": i, "title": f"Result {i}"} for i in range(1, 21)]  # 20 items


# ---------------------------------------------------------------------------
# paginate — BUG 1: off-by-one, end index is start + page_size + 1
# ---------------------------------------------------------------------------

def paginate(items, page, page_size=5):
    """
    Return the slice of *items* for *page* (1-indexed).
    page=1, page_size=5 should return items[0:5].
    """
    start = (page - 1) * page_size
    end   = start + page_size + 1   # BUG: should be start + page_size
    return items[start:end]


@app.route("/results")
def list_results():
    page = int(request.args.get("page", 1))
    return jsonify(paginate(ITEMS, page))


# ---------------------------------------------------------------------------
# search — BUG 2: crashes on non-string query (e.g. query=None or integer)
# ---------------------------------------------------------------------------

def search(items, query):
    """Return items whose title contains the query string (case-insensitive)."""
    return [i for i in items if query.lower() in i["title"].lower()]
    # BUG: AttributeError when query is None — .lower() on NoneType


@app.route("/search")
def search_results():
    query = request.args.get("q")   # returns None when ?q is absent
    return jsonify(search(ITEMS, query))


if __name__ == "__main__":
    app.run(debug=True)

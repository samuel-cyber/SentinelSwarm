"""
Scenario: user_api
A small user-management Flask app with 2 planted bugs.

Run locally:
    flask --app app run
"""

from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

USERS = {
    1: {"id": 1, "name": "Alice", "email": "alice@example.com", "age": 30},
    2: {"id": 2, "name": "Bob",   "email": "bob@example.com",   "age": 25},
    3: {"id": 3, "name": "Carol", "email": "carol@example.com", "age": 22},
    4: {"id": 4, "name": "Dave",  "email": "dave@example.com",  "age": 17},
    5: {"id": 5, "name": "Eve",   "email": "eve@example.com",   "age": 34},
}


# ---------------------------------------------------------------------------
# get_user_by_id — BUG 1: URL params are strings; dict keys are ints
# ---------------------------------------------------------------------------

def get_user_by_id(user_id):
    """Return the user dict for the given id, or None if not found."""
    return USERS.get(user_id)        # BUG: string "1" never matches int key 1


@app.route("/users/<user_id>")
def user_detail(user_id):
    user = get_user_by_id(user_id)   # user_id is a str here
    if user is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user)


# ---------------------------------------------------------------------------
# register_user — BUG 2: age compared before None-check
# ---------------------------------------------------------------------------

def register_user(data):
    """
    Validate and insert a new user.
    Returns (user_dict, None) on success or (None, error_message) on failure.
    """
    name  = data.get("name",  "").strip()
    email = data.get("email", "").strip()
    age   = data.get("age")                 # None when key absent

    if not name:
        return None, "name is required"
    if not email:
        return None, "email is required"
    if age < 18:                            # BUG: TypeError when age is None
        return None, "must be 18 or older"

    new_id = max(USERS.keys()) + 1
    user   = {"id": new_id, "name": name, "email": email, "age": age}
    USERS[new_id] = user
    return user, None


@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json(force=True, silent=True) or {}
    user, err = register_user(data)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(user), 201


@app.route("/users")
def user_list():
    return jsonify(list(USERS.values()))


if __name__ == "__main__":
    app.run(debug=True)

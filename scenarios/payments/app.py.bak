"""
Scenario: payments
A minimal payment-processing helper with 2 planted bugs.

Run locally:
    flask --app app run
"""

from flask import Flask, request, jsonify

app = Flask(__name__)

TRANSACTIONS = []


# ---------------------------------------------------------------------------
# apply_discount — BUG 1: percent not divided by 100, multiplier wrong
# ---------------------------------------------------------------------------

def apply_discount(amount, discount_percent):
    """
    Return *amount* reduced by *discount_percent* percent.
    e.g. apply_discount(100, 10) should return 90.0
    """
    return amount * (1 - discount_percent)   # BUG: should be discount_percent / 100
                                              # e.g. discount=10 → multiplier -9, not 0.9


# ---------------------------------------------------------------------------
# process_payment — BUG 2: amount validated after it is used
# ---------------------------------------------------------------------------

def process_payment(amount, currency="USD"):
    """
    Record a payment transaction.
    Returns the saved transaction dict, or raises ValueError for bad input.
    """
    transaction = {
        "id":       len(TRANSACTIONS) + 1,
        "amount":   round(float(amount), 2),   # BUG: crashes if amount is None
        "currency": currency.upper(),           #      before the validation below
    }
    if amount is None or float(amount) <= 0:   # validation comes too late
        raise ValueError("amount must be a positive number")
    TRANSACTIONS.append(transaction)
    return transaction


@app.route("/pay", methods=["POST"])
def pay():
    data     = request.get_json(force=True, silent=True) or {}
    amount   = data.get("amount")
    currency = data.get("currency", "USD")
    discount = data.get("discount", 0)

    try:
        final_amount = apply_discount(float(amount), discount)
        txn = process_payment(final_amount, currency)
        return jsonify(txn), 201
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/transactions")
def list_transactions():
    return jsonify(TRANSACTIONS)


if __name__ == "__main__":
    app.run(debug=True)

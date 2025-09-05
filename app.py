import sqlite3
import hashlib
import json
from flask import Flask, render_template, request, jsonify, g
from ecdsa import SigningKey, VerifyingKey, SECP256k1

# --- App and Database Configuration ---
DATABASE = 'database.db'
app = Flask(__name__)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

# --- HTML Page Routes ---
@app.route('/')
def sebi_admin_page():
    return render_template('sebi_register_broker.html')

@app.route('/broker')
def broker_page():
    return render_template('broker_page.html')

@app.route('/verify')
def sebi_verify_page():
    return render_template('sebi_verify.html')

# --- API Endpoints ---

@app.route('/api/register_broker', methods=['POST'])
def register_broker():
    data = request.json
    name = data['brokerName']
    website = data['brokerWebsite']
    
    # Generate cryptographic keys
    sk = SigningKey.generate(curve=SECP256k1)
    vk = sk.verifying_key
    
    private_key_hex = sk.to_string().hex()
    public_key_hex = vk.to_string().hex()
    
    db = get_db()
    db.execute(
        'INSERT INTO brokers (name, website, public_key, private_key) VALUES (?, ?, ?, ?)',
        (name, website, public_key_hex, private_key_hex)
    )
    db.commit()
    
    return jsonify({"status": "success", "message": f"Broker {name} registered."})

@app.route('/api/brokers', methods=['GET'])
def get_brokers():
    brokers = get_db().execute('SELECT name, public_key FROM brokers').fetchall()
    return jsonify([dict(broker) for broker in brokers])

@app.route('/api/generate_txid', methods=['POST'])
def generate_txid():
    data = request.json
    broker_pub_key = data['brokerPubKey']
    pan = data['panId']
    dp = data['dpId']

    # Hash the investor's unique data
    investor_data = f"{pan.upper().strip()}-{dp.upper().strip()}"
    hashed_investor_id = hashlib.sha256(investor_data.encode()).hexdigest()
    
    # Retrieve the broker's private key from DB to sign the hash
    db = get_db()
    broker = db.execute('SELECT private_key FROM brokers WHERE public_key = ?', (broker_pub_key,)).fetchone()
    if not broker:
        return jsonify({"status": "error", "message": "Broker not found"}), 404
        
    sk = SigningKey.from_string(bytes.fromhex(broker['private_key']), curve=SECP256k1)
    signature = sk.sign(hashed_investor_id.encode()).hex()
    
    # Create the final TxID
    transaction_data = f"{broker_pub_key}{hashed_investor_id}{signature}"
    tx_id = hashlib.sha256(transaction_data.encode()).hexdigest()
    
    # Save transaction to ledger
    db.execute(
        'INSERT INTO ledger (tx_id, broker_pub_key, hashed_investor_id, signature) VALUES (?, ?, ?, ?)',
        (tx_id, broker_pub_key, hashed_investor_id, signature)
    )
    db.commit()
    
    return jsonify({"status": "success", "tx_id": tx_id})

@app.route('/api/verify_txid/<txid>', methods=['GET'])
def verify_txid(txid):
    db = get_db()
    transaction = db.execute('SELECT * FROM ledger WHERE tx_id = ?', (txid,)).fetchone()
    
    if not transaction:
        return jsonify({"status": "error", "message": "Transaction ID not found. This registration is NOT valid."}), 404

    broker = db.execute('SELECT name, website FROM brokers WHERE public_key = ?', (transaction['broker_pub_key'],)).fetchone()
    
    try:
        vk = VerifyingKey.from_string(bytes.fromhex(transaction['broker_pub_key']), curve=SECP256k1)
        vk.verify(bytes.fromhex(transaction['signature']), transaction['hashed_investor_id'].encode())
        
        # If verification passes, return success
        return jsonify({
            "status": "success",
            "message": "Verification Successful",
            "details": {
                "brokerName": broker['name'],
                "brokerWebsite": broker['website'],
                "timestamp": transaction['timestamp']
            }
        })
    except Exception:
        # If verification fails
        return jsonify({"status": "error", "message": "Verification Failed. The digital signature is invalid."}), 400

# --- Database Initialization ---
def init_database():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # Create brokers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS brokers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                website TEXT NOT NULL,
                public_key TEXT UNIQUE NOT NULL,
                private_key TEXT UNIQUE NOT NULL
            );
        ''')
        # Create ledger table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id TEXT UNIQUE NOT NULL,
                broker_pub_key TEXT NOT NULL,
                hashed_investor_id TEXT NOT NULL,
                signature TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (broker_pub_key) REFERENCES brokers (public_key)
            );
        ''')
        db.commit()

if __name__ == '__main__':
    init_database()
    app.run(debug=False, port=8050, host='0.0.0.0')
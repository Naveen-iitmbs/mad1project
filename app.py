from flask import Flask, render_template, request, redirect, session, g, flash
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'simple-secret'
DATABASE = 'parking.db'


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db:
        db.close()


def init_db():
    db = get_db()

    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS parking_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            pin TEXT NOT NULL,
            price REAL NOT NULL,
            max_spots INTEGER NOT NULL
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS parking_spots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (lot_id) REFERENCES parking_lots(id)
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            spot_id INTEGER NOT NULL,
            start_time TEXT,
            end_time TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (spot_id) REFERENCES parking_spots(id)
        )
    ''')

    # Add admin user
    existing_admin = db.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not existing_admin:
        db.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('admin', 'admin'))

    db.commit()


def is_admin():
    return session.get('user_type') == 'admin'


def is_user():
    return session.get('user_type') == 'user'


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form['role']
        username = request.form['username']
        password = request.form['password']

        if role == 'admin':
            if username == 'admin' and password == 'admin':
                session['user_type'] = 'admin'
                return redirect('/admin')
            else:
                flash("Invalid admin credentials")
                return redirect('/')

        elif role == 'user':
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
            if user:
                session['user_type'] = 'user'
                session['user_id'] = user['id']
                return redirect('/user')
            else:
                flash("Invalid user credentials")
                return redirect('/')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        try:
            db.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            db.commit()
            flash("Registration successful. Please log in.")
            return redirect('/')
        except sqlite3.IntegrityError:
            flash("Username already taken.")
            return redirect('/register')
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


@app.route('/admin')
def admin_dashboard():
    if not is_admin():
        return redirect('/')

    db = get_db()
    lots_raw = db.execute('SELECT * FROM parking_lots').fetchall()
    lots = []

    for lot in lots_raw:
        total = lot['max_spots']
        occupied = db.execute('SELECT COUNT(*) FROM parking_spots WHERE lot_id = ? AND status = "O"', (lot['id'],)).fetchone()[0]
        available = total - occupied
        lots.append({
            'id': lot['id'],
            'name': lot['name'],
            'address': lot['address'],
            'pin': lot['pin'],
            'price': lot['price'],
            'total_spots': total,
            'occupied_spots': occupied,
            'available_spots': available
        })

    users = db.execute('SELECT id, username FROM users WHERE username != "admin"').fetchall()

    return render_template('admin.html', lots=lots, users=users)


@app.route('/admin/create', methods=['POST'])
def create_lot():
    if not is_admin():
        return redirect('/')

    name = request.form['name']
    address = request.form['address']
    pin = request.form['pin']
    price = float(request.form['price'])
    max_spots = int(request.form['max_spots'])

    db = get_db()
    db.execute('''
        INSERT INTO parking_lots (name, address, pin, price, max_spots)
        VALUES (?, ?, ?, ?, ?)
    ''', (name, address, pin, price, max_spots))
    db.commit()

    lot_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    for _ in range(max_spots):
        db.execute('INSERT INTO parking_spots (lot_id, status) VALUES (?, ?)', (lot_id, 'A'))
    db.commit()
    flash("Parking lot created.")
    return redirect('/admin')


@app.route('/admin/delete/<int:lot_id>')
def delete_lot(lot_id):
    if not is_admin():
        return redirect('/')

    db = get_db()
    occupied = db.execute('''
        SELECT COUNT(*) FROM parking_spots
        WHERE lot_id = ? AND status = 'O'
    ''', (lot_id,)).fetchone()[0]

    if occupied > 0:
        flash("Cannot delete: Lot has occupied spots.")
        return redirect('/admin')

    db.execute('DELETE FROM parking_spots WHERE lot_id = ?', (lot_id,))
    db.execute('DELETE FROM parking_lots WHERE id = ?', (lot_id,))
    db.commit()
    flash("Lot deleted.")
    return redirect('/admin')


@app.route('/admin/update_spots/<int:lot_id>', methods=['POST'])
def update_spots(lot_id):
    if not is_admin():
        return redirect('/')

    db = get_db()
    lot = db.execute('SELECT max_spots FROM parking_lots WHERE id = ?', (lot_id,)).fetchone()
    if not lot:
        flash("Lot not found.")
        return redirect('/admin')

    current_max = lot['max_spots']
    new_max = int(request.form['new_spots'])

    occupied = db.execute('''
        SELECT COUNT(*) FROM parking_spots
        WHERE lot_id = ? AND status = 'O'
    ''', (lot_id,)).fetchone()[0]

    if new_max < occupied:
        flash("Cannot reduce below occupied spots.")
        return redirect('/admin')

    if new_max > current_max:
        for _ in range(new_max - current_max):
            db.execute('INSERT INTO parking_spots (lot_id, status) VALUES (?, ?)', (lot_id, 'A'))
    elif new_max < current_max:
        removable = db.execute('''
            SELECT id FROM parking_spots
            WHERE lot_id = ? AND status = 'A'
            LIMIT ?
        ''', (lot_id, current_max - new_max)).fetchall()
        for spot in removable:
            db.execute('DELETE FROM parking_spots WHERE id = ?', (spot['id'],))

    db.execute('UPDATE parking_lots SET max_spots = ? WHERE id = ?', (new_max, lot_id))
    db.commit()
    flash("Spots updated.")
    return redirect('/admin')


@app.route('/user')
def user_dashboard():
    if not is_user():
        return redirect('/')

    db = get_db()
    lots = db.execute('SELECT * FROM parking_lots').fetchall()
    user_reserved = db.execute('''
        SELECT r.*, l.name FROM reservations r
        JOIN parking_spots s ON r.spot_id = s.id
        JOIN parking_lots l ON s.lot_id = l.id
        WHERE r.user_id = ? AND r.end_time IS NULL
    ''', (session['user_id'],)).fetchone()

    availability = {}
    for lot in lots:
        available = db.execute('''
            SELECT COUNT(*) FROM parking_spots
            WHERE lot_id = ? AND status = 'A'
        ''', (lot['id'],)).fetchone()[0]
        availability[lot['id']] = available

    return render_template('user.html', lots=lots, reserved=user_reserved, availability=availability)


@app.route('/user/reserve/<int:lot_id>')
def reserve_spot(lot_id):
    if not is_user():
        return redirect('/')

    db = get_db()
    existing = db.execute('''
        SELECT * FROM reservations
        WHERE user_id = ? AND end_time IS NULL
    ''', (session['user_id'],)).fetchone()

    if existing:
        flash("You already have an active reservation.")
        return redirect('/user')

    spot = db.execute('''
        SELECT * FROM parking_spots
        WHERE lot_id = ? AND status = "A"
        LIMIT 1
    ''', (lot_id,)).fetchone()

    if not spot:
        flash("No available spots.")
        return redirect('/user')

    db.execute('UPDATE parking_spots SET status = "O" WHERE id = ?', (spot['id'],))
    db.execute('INSERT INTO reservations (user_id, spot_id, start_time) VALUES (?, ?, ?)',
               (session['user_id'], spot['id'], datetime.now().isoformat()))
    db.commit()
    flash("Spot reserved.")
    return redirect('/user')


@app.route('/user/release')
def release_spot():
    if not is_user():
        return redirect('/')

    db = get_db()
    reservation = db.execute('''
        SELECT * FROM reservations
        WHERE user_id = ? AND end_time IS NULL
        ORDER BY id DESC LIMIT 1
    ''', (session['user_id'],)).fetchone()

    if reservation:
        db.execute('UPDATE parking_spots SET status = "A" WHERE id = ?', (reservation['spot_id'],))
        db.execute('UPDATE reservations SET end_time = ? WHERE id = ?', (datetime.now().isoformat(), reservation['id']))
        db.commit()
        flash("Spot released.")
    else:
        flash("No active reservation.")
    return redirect('/user')


if __name__ == '__main__':
    if os.path.exists('parking.db'):
        os.remove('parking.db')
    with app.app_context():
        init_db()
    app.run(debug=True)

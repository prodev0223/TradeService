from datetime import datetime
from flask import Flask, request, abort
from flask_mongoengine import MongoEngine
from mongoengine import *
from waitress import serve

app = Flask(__name__)

app.config['MONGODB_SETTINGS'] = {
    'db': 'trade_db',
    'host': 'localhost',
    'port': 27017
}
db = MongoEngine()
db.init_app(app)


# MongoEngine Schema
class Message(Document):
    bot_id = StringField(required=True)
    pair = StringField(required=True)
    command = StringField(required=True)
    percent = StringField(default="none")
    timestamp = DateTimeField(default=datetime.utcnow)
    status = StringField(default="pending")
    error_msg = StringField()


@app.route('/')
def test():
    return 'webhook server is online!'


@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        command = request.form.get('command')
        command = command.encode('UTF-8')
        # print(f"new message : {request.data.decode('UTF-8')}")
        print(f"new message : {command.decode('UTF-8')}")
        try:
            # msg = request.data.decode('UTF-8').split("_")
            msg = command.decode('UTF-8').split("_")
            botids = msg[0].split("&")
            pair = msg[1]
            command = msg[2]
            try:
                percent = msg[3]
                for botid in botids:
                    record = Message(bot_id=botid, pair=pair, command=command, percent=percent).save()
            except:
                for botid in botids:
                    record = Message(bot_id=botid, pair=pair, command=command).save()
            return 'success', 200

        except IndexError:
            print("Illegal message.")
            abort(400)

    else:
        abort(400)


serve(app, host='0.0.0.0', port=80, threads=10)

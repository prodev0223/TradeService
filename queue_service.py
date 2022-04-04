from datetime import datetime
from threading import Event
from mongoengine import *
import subprocess


# MongoEngine Schema
class Message(Document):
    bot_id = StringField(required=True)
    pair = StringField(required=True)
    command = StringField(required=True)
    percent = StringField(default="none")
    timestamp = DateTimeField(default=datetime.utcnow)
    status = StringField(default="pending")
    error_msg = StringField()


class Lock(Document):
    bot_id = StringField(required=True)


exit_event = Event()


def launch_bot(bot_id):
    # Launch Bot
    subprocess.Popen(f"python.exe trade.py {bot_id} -silent", shell=True)


def service_main():
    Lock.objects().delete()
    print("QueueService running... press Ctrl+C to stop")
    while not exit_event.is_set():
        for bot_id in Message.objects(status="pending").distinct(field="bot_id"):
            # print(f"There are some pending messages for bot {bot_id}")

            # Check for lock
            if Lock.objects(bot_id=bot_id).first() is None:
                # print(f"Launching bot {bot_id}")

                # Create Lock
                b_lock = Lock(bot_id=bot_id).save()

                launch_bot(bot_id)
            else:
                # print("bot is locked!")
                pass

        exit_event.wait(2)
    # Cleanup
    print('Destroying all lock objects...')
    Lock.objects().delete()
    print('Bye!')


def service_quit(signo, _frame):
    print(f"Interrupted by {signo}, shutting down...")
    exit_event.set()


if __name__ == '__main__':
    connect('trade_db')
    print("Connected to DB!")

    # Handle termination signals
    import signal
    for sig in ('TERM', 'INT'):
        signal.signal(getattr(signal, 'SIG' + sig), service_quit)

    service_main()


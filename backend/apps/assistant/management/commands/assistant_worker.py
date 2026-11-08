import time
from django.core.management.base import BaseCommand
from apps.assistant.worker import process_one, recover_stale_claims

class Command(BaseCommand):
    help = "Processes locally queued WhatsApp messages under configured safety rules."
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one eligible message and exit.")
        parser.add_argument("--poll-seconds", type=float, default=1.0)
    def handle(self, *args, **options):
        recover_stale_claims()
        if options["once"]:
            process_one()
            return
        self.stdout.write("Assistant worker running; Ctrl+C to stop.")
        while True:
            try:
                did_work = process_one()
                if not did_work:
                    time.sleep(max(0.25, options["poll_seconds"]))
            except KeyboardInterrupt:
                break
            except Exception:
                self.stderr.write("Worker loop error; will retry on the next poll.")
                time.sleep(max(0.25, options["poll_seconds"]))

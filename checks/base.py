class ActivityCheck:
    name = "base"
    description = ""

    def __init__(self, config=None):
        self.config = config or {}

    def run(self, activities):
        raise NotImplementedError

    def finding(self, summary, activities, reasons, severity="info"):
        #import ipdb
        #ipdb.set_trace()
        return {
            "check": self.name,
            "severity": severity,
            "summary": summary,
            "activities": [
                {
                    "id": a.id,
                    "name": a.name,
                    "type": a.activity_type,
                    "start": a.start_date or a.start_date_local,
                    "end": a.end.isoformat(),
                    "duration_seconds": a.duration_seconds,
                    "distance": a.distance,
                    "url": a.url,
                } for a in activities
            ],
            "reasons": reasons,
        }

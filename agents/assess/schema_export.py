"""Regenerate current Assess and investigation JSON Schemas."""
import json
from pathlib import Path
from .contracts import AssessRun, ResearchSubject, ResearchSubjectLedger

def write_schemas(directory=None):
    target=Path(directory) if directory else Path(__file__).parent/'schemas'
    target.mkdir(parents=True,exist_ok=True)
    for model in (AssessRun,ResearchSubject,ResearchSubjectLedger):
        (target/(model.__name__.lower()+'.schema.json')).write_text(json.dumps(model.model_json_schema(),indent=2,sort_keys=True)+'\n')

if __name__=='__main__':
    write_schemas()

from pathlib import Path
import sys,json,sqlite3,time,tempfile,hashlib,importlib.metadata as md
import numpy as np
import argparse
parser=argparse.ArgumentParser(description="Offline real-model semantic runtime check on a saved-source sample")
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[3])
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();root=args.root.resolve();sys.path.insert(0,str(root))
from tools.retrieval import dense,hybrid,fts
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True);start=time.monotonic();encoder=dense.default_encoder()
vecs=encoder(['Enterprise packet capture and network performance monitoring.','Network observability and application troubleshooting.','Catering and cafeteria meal services.'])
assert vecs.shape==(3,768) and np.isfinite(vecs).all() and np.allclose(np.linalg.norm(vecs,axis=1),1,atol=1e-4)
sims=vecs@vecs.T;assert sims[0,1]>sims[0,2]
cols='notice_id,title,description_prefix,naics,psc,agency,subtier,office,notice_type,posted,deadline,set_aside'
source=sqlite3.connect('file:'+str(root/'data/state/notice_store/notices.db')+'?mode=ro',uri=True)
rows=source.execute('SELECT '+cols+' FROM notices ORDER BY notice_id LIMIT 256').fetchall();population=source.execute('SELECT COUNT(*) FROM notices').fetchone()[0];source.close()
with tempfile.TemporaryDirectory(prefix='lila058-',dir=out) as tmp:
 tmp=Path(tmp);store=sqlite3.connect(':memory:');store.row_factory=sqlite3.Row
 store.execute('CREATE TABLE notices ('+','.join(c+' TEXT'+(' PRIMARY KEY' if c=='notice_id' else '') for c in cols.split(','))+')')
 store.executemany('INSERT INTO notices VALUES ('+','.join('?' for _ in cols.split(','))+')',rows);store.commit()
 side=dense.open_sidecar(tmp/'vectors.db');t=time.monotonic();receipt=dense.embed_missing(store,side,encoder=encoder,receipt_path=tmp/'embedding.json');wall=time.monotonic()-t
 again=dense.embed_missing(store,side,encoder=encoder,receipt_path=tmp/'incremental.json');assert again['rows_embedded_this_pass']==0
 fts.rebuild(store,receipt_path=tmp/'fts.json')
 hits=dense.dense_retrieve(['Enterprise network monitoring and application performance'],k=5,sidecar=side,encoder=encoder);assert len(hits)==5 and all(np.isfinite(s) for _,s in hits)
 frame={'client_name':'Runtime diagnostic','as_ordered':{'tier1':[],'tier2':['network monitoring'],'tier3':[]},'screen_routing':{'tier1_client_names':[],'tier1_rival_names':[]}}
 combined=hybrid.hybrid_retrieve(frame,k=5,capability_statements=['Enterprise network monitoring and application performance'],store_conn=store,sidecar=side,encoder=encoder)
 assert combined['receipt']['dense_pool']==256
 record={'status':'REAL_ENCODER_AND_RETRIEVAL_SMOKE_PASS','model_id':dense.MODEL_ID,'model_cache_revision':(Path.home()/'.cache/huggingface/hub/models--BAAI--bge-base-en-v1.5/refs/main').read_text().strip(),'device':encoder.device,'dimensions':768,'vectors_finite_normalized':True,'related_similarity':float(sims[0,1]),'unrelated_similarity':float(sims[0,2]),'saved_source_population':population,'sample_rows':len(rows),'sample_method':'First256 by notice_id; runtime sample not relevance benchmark','sample_sha256':hashlib.sha256(json.dumps(rows,default=str).encode()).hexdigest(),'embedding':receipt,'incremental_rows_reembedded':again['rows_embedded_this_pass'],'dense_hits':hits,'hybrid_receipt':combined['receipt'],'sample_embedding_seconds':wall,'estimated_full_embedding_seconds':wall/len(rows)*population,'total_seconds':time.monotonic()-start,'source_store_changed':False,'operating_index_created':False,'client_run_started':False,'packages':{n:md.version(n) for n in ['sentence-transformers','torch','transformers']}}
 (out/'REAL_SMOKE.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));store.close();side.close()

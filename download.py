from huggingface_hub import snapshot_download
snapshot_download(repo_id='phamquiluan/RCAEval', repo_type='dataset', revision='92c773ab7bb79f525ec7d5dc53d96a74dbebce4d', allow_patterns=['re1ob_*/metrics.parquet','re1ob_*/inject_time.txt'], local_dir='data')

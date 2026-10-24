"""Explicit dev-only Cloud Run deployment of the two ADK evaluation fixtures.

Uses a staged allowlist; never uploads .env, ADC, run evidence or the whole checkout.
Run with --execute after reviewing the command plan. Raw deployment logs are private.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def deployment_sources(root=ROOT):
    """Explicit upload allowlist; secret files beside Python sources are excluded."""
    names = {'pyproject.toml', 'uv.lock', '.python-version',
             'deployment/hosted/Dockerfile', 'deployment/hosted/cloudbuild.yaml'}
    for folder in ('examples/load', 'examples/support', 'deployment/hosted'):
        base = root / folder
        if base.is_symlink():
            raise ValueError('Deployment sources cannot contain symlinks')
        for path in base.rglob('*'):
            if path.is_symlink():
                raise ValueError('Deployment sources cannot contain symlinks')
            if path.is_file() and path.suffix == '.py':
                names.add(path.relative_to(root).as_posix())
    for name in names:
        if (root / name).is_symlink() or not (root / name).is_file():
            raise ValueError('Missing or symlinked deployment source: ' + name)
    return sorted(names)


def stage_sources(stage, root=ROOT):
    names = deployment_sources(root)
    # Reusing a stage could upload stale files left by a prior run or local edits.
    stage.mkdir(parents=True, exist_ok=False)
    for name in names:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--region',default='us-central1')
    p.add_argument('--name',default='adk-eval-poc-20261021');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--execute',action='store_true');args=p.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    project,region,name=args.project,args.region,args.name
    builder=f'{name}-build@{project}.iam.gserviceaccount.com'
    runtime=f'{name}-run@{project}.iam.gserviceaccount.com'
    bucket=f'{project}-{name}-build';image=f'{region}-docker.pkg.dev/{project}/{name}/agents:verified'
    stage=out/'source';stage_sources(stage)
    steps=[]
    def call(label,argv,*,optional=False):
        steps.append({'step':label,'argv':argv})
        (out/'plan.json').write_text(json.dumps(steps,indent=2)+'\n')
        if not args.execute:return None
        r=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=1800)
        (out/f'{label}.log').write_text(r.stdout+r.stderr)
        print(f'{label}: exit {r.returncode}',flush=True)
        if r.returncode and not optional:raise RuntimeError(f'{label} failed; inspect private deployment log')
        return r
    def g(label,*argv,optional=False):return call(label,['gcloud',*argv,'--project',project,'--quiet'],optional=optional)
    g('enable_apis','services','enable','run.googleapis.com','cloudbuild.googleapis.com','artifactregistry.googleapis.com','iam.googleapis.com')
    for suffix,email in [('build',builder),('run',runtime)]:
        r=g('inspect_'+suffix,'iam','service-accounts','describe',email,optional=True)
        if r is None or r.returncode:g('create_'+suffix,'iam','service-accounts','create',name+'-'+suffix,'--display-name','ADK eval POC '+suffix)
    r=g('inspect_registry','artifacts','repositories','describe',name,'--location',region,optional=True)
    if r is None or r.returncode:g('create_registry','artifacts','repositories','create',name,'--location',region,'--repository-format','docker')
    r=g('inspect_bucket','storage','buckets','describe','gs://'+bucket,optional=True)
    if r is None or r.returncode:g('create_bucket','storage','buckets','create','gs://'+bucket,'--location',region,'--uniform-bucket-level-access')
    g('builder_bucket_access','storage','buckets','add-iam-policy-binding','gs://'+bucket,'--member','serviceAccount:'+builder,'--role','roles/storage.objectAdmin')
    g('builder_bucket_metadata','storage','buckets','add-iam-policy-binding','gs://'+bucket,'--member','serviceAccount:'+builder,'--role','roles/storage.legacyBucketReader')
    g('builder_registry_access','artifacts','repositories','add-iam-policy-binding',name,'--location',region,'--member','serviceAccount:'+builder,'--role','roles/artifactregistry.writer')
    g('runtime_model_access','projects','add-iam-policy-binding',project,'--member','serviceAccount:'+runtime,'--role','roles/aiplatform.user')
    g('build','builds','submit',str(stage),'--config',str(stage/'deployment/hosted/cloudbuild.yaml'),
      '--substitutions','_IMAGE='+image,'--service-account',f'projects/{project}/serviceAccounts/{builder}',
      '--gcs-source-staging-dir',f'gs://{bucket}/source','--gcs-log-dir',f'gs://{bucket}/logs','--format=json')
    # Explicit private IAM, bounded dev scaling; standard ADK API server.
    g('deploy','run','deploy',name,'--image',image,'--region',region,'--service-account',runtime,
      '--no-allow-unauthenticated','--min-instances','0','--max-instances','1','--concurrency','8',
      '--cpu','1','--memory','2Gi','--timeout','300',
      '--set-env-vars',f'GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT={project},GOOGLE_CLOUD_LOCATION=global,AGENTS_CLI_TELEMETRY=0',
      '--format=json')
    r=g('service','run','services','describe',name,'--region',region,'--format=json')
    if r is not None and r.returncode==0:
        service=json.loads(r.stdout)
        (out/'endpoint.json').write_text(json.dumps({'url':service['status']['url'],'revision':service['status']['latestReadyRevisionName'],'image':image,'service':name,'region':region},indent=2)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())

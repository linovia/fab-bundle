import os

from fabric.api import task, env, run, local, put, cd, sudo
from fabric.contrib.files import exists

from .utils import die, err, yay, template
from . import django, db


def handle_rq(bundle_name, bundle_root, env):
    # RQ forks processes and they load the latest version of the code.
    # No need to restart the worker **unless** RQ has been updated (TODO).
    for worker_id in range(env.rq['workers']):
        env.worker_id = worker_id
        template(
            'rq.conf', '%s/conf/rq%s.conf' % (bundle_root, worker_id),
        )
        with cd('/etc/supervisor/conf.d'):
            sudo('ln -sf %s/conf/rq%s.conf %s_worker%s.conf' % (
                bundle_root, worker_id, bundle_name, worker_id,
            ))

    # Scale down workers if the number decreased
    workers = run('ls /etc/supervisor/conf.d/%s_worker*.conf' % bundle_name)
    workers_conf = run('ls %s/conf/rq*.conf' % bundle_root)
    to_delete = []
    for w in workers.split():
        if (int(w.split('%s_worker' % bundle_name, 1)[1][:-5]) >=
                env.rq['workers']):
            to_delete.append(w)
    for w in workers_conf.split():
        if int(w.split(bundle_name, 1)[1][8:-5]) >= env.rq['workers']:
            to_delete.append(w)
    if to_delete:
        sudo('rm %s' % " ".join(to_delete))


def handle_celery(bundle_name, bundle_root, env):
    for worker_id, worker in enumerate(env.celery['workers']):
        env.worker_id = worker_id
        worker_args = [
            '--%s' % (k,)
            if isinstance(v, bool) else '--%s=%s' % (k, v)
            for k, v in worker.items()]
        env.worker_args = ' '.join(worker_args)
        template(
            'celery.conf', '%s/conf/celery%04i.conf' % (bundle_root, worker_id)
        )
        with cd('/etc/supervisor/conf.d'):
            sudo('ln -sf %s/conf/celery%04i.conf %s_worker%04i.conf' % (
                bundle_root, worker_id, bundle_name, worker_id,
            ))
    env.worker_id = None

    # Scale down workers if the number decreased
    workers = run('ls /etc/supervisor/conf.d/%s_worker*.conf' % bundle_name)
    workers_conf = run('ls %s/conf/celery*.conf' % bundle_root)
    to_delete = []
    # for w in workers.split():
    #     if (int(w.split('%s_worker' % bundle_name, 1)[1][:-5]) >=
    #             env.rq['workers']):
    #         to_delete.append(w)
    # for w in workers_conf.split():
    #     if int(w.split(bundle_name, 1)[1][8:-5]) >= env.rq['workers']:
    #         to_delete.append(w)
    # if to_delete:
    #     sudo('rm %s' % " ".join(to_delete))


def create_virtualenv():
    python_switch = ''
    if hasattr(env, 'python'):
        python_switch = '--python=%s' % getattr(env, 'python')
    if not exists(env.bundle_root + '/env'):
        run('virtualenv %s --no-site-packages %s/env' % (
            python_switch, env.bundle_root))
    run('%s/env/bin/pip install -U pip' % env.bundle_root)


def upload_vendor_packages():
    packages_location = env.bundle_root + '/packages'
    has_vendor = 'vendor' in os.listdir(os.getcwd())
    if has_vendor:
        local_files = set(os.listdir(os.path.join(os.getcwd(), 'vendor')))
        uploaded = set(run('ls %s' % packages_location).split())
        diff = local_files - uploaded
        for file_name in diff:
            put('vendor/%s' % file_name,
                '%s/%s' % (packages_location, file_name))


def install_package(requirement, force_version, packages):
    freeze = run('%s/env/bin/pip freeze' % env.bundle_root).split()
    if requirement in freeze and force_version is None:
        die("%s is already deployed. Increment the version number to deploy "
            "a new release." % requirement)

    cmd = (
        '%s/env/bin/pip install -U %s gunicorn gevent greenlet '
        'setproctitle --find-links file://%s') % (
        env.bundle_root, requirement, packages
    )
    if 'index_url' in env:
        cmd += ' --index-url %(index_url)s' % env
    run(cmd)
    env.path = env.bundle_root
    python = run('ls %s/env/lib' % env.bundle_root)
    template(
        'path_extension.pth',
        '%s/env/lib/%s/site-packages/_virtualenv_path_extensions.pth' % (
            env.bundle_root, python
        ),
    )


def setup_cron():
    if 'cron' in env:
        template('cron', '%(bundle_root)s/conf/cron' % env, use_sudo=True)
        sudo('chown root:root %(bundle_root)s/conf/cron' % env)
        sudo('chmod 644 %(bundle_root)s/conf/cron' % env)
        sudo('ln -sf %(bundle_root)s/conf/cron /etc/cron.d/%(app)s' % env)
    else:
        # Make sure to deactivate tasks if the cron section is removed
        sudo('rm -f %(bundle_root)s/conf/cron /etc/cron.d/%(app)s' % env)


def setup_nginx():
    changed = template('nginx.conf', '%s/conf/nginx.conf' % env.bundle_root)
    with cd('/etc/nginx/sites-available'):
        sudo('ln -sf %s/conf/nginx.conf %s.conf' % (
            env.bundle_root, env.http_host))
    with cd('/etc/nginx/sites-enabled'):
        sudo('ln -sf ../sites-available/%s.conf' % env.http_host)
    if env.get('ssl_cert') and env.get('ssl_key'):
        put(env.ssl_cert, '%s/conf/ssl.crt' % env.bundle_root)
        put(env.ssl_key, '%s/conf/ssl.key' % env.bundle_root)
    if changed:  # TODO detect if the certs have changed
        sudo('/etc/init.d/nginx reload')


@task()
def deploy(force_version=None):
    """Deploys to the current bundle"""

    # Bundle creation
    bundle_name = env.http_host
    bundle_root = '%s/%s' % (env.get('bundle_root', run('pwd') + '/bundles'),
                             bundle_name)
    env.bundle_root = bundle_root
    run('mkdir -p %s/{log,conf,public}' % bundle_root)

    # virtualenv, Packages
    create_virtualenv()

    #####
    # Generate local package
    local('python setup.py sdist')
    dists = [
        d for d in os.listdir(os.path.join(os.getcwd(),
                                           'dist')) if d.endswith('.tar.gz')
    ]
    version_string = lambda d: d.rsplit('-', 1)[1][:-7]

    def int_or_s(num):
        try:
            return int(num)
        except ValueError:
            return num
    dist = sorted(dists, key=lambda d: map(int_or_s,
                                           version_string(d).split('.')))[-1]
    version = force_version or version_string(dist)
    dist_name = dist.rsplit('-', 1)[0]
    requirement = '%s==%s' % (dist_name, version)

    print('*' * 120)
    print(dist)
    print(requirement)
    print('*' * 120)

    packages = bundle_root + '/packages'
    run('mkdir -p %s' % packages)
    if not exists('%s/%s' % (packages, dist)):
        put('dist/%s' % dist, '%s/%s' % (packages, dist))
    # End of local package
    #####

    upload_vendor_packages()
    install_package(requirement, force_version, packages)
    django.setup()

    # Do we have a DB?
    db.creation(bundle_name)
    django.database_migration()

    django.collectstatic()

    # Some things don't like dots
    env.app = env.http_host.replace('.', '')

    # Cron tasks
    setup_cron()

    # Log rotation
    logrotate = '/etc/logrotate.d/%(app)s' % env
    template('logrotate', logrotate, use_sudo=True)
    sudo('chown root:root %s' % logrotate)

    # Nginx vhost
    setup_nginx()

    # Supervisor task(s) -- gunicorn + rq
    if not 'workers' in env:
        env.workers = 2
    changed = template('supervisor.conf',
                       '%s/conf/supervisor.conf' % bundle_root)
    with cd('/etc/supervisor/conf.d'):
        sudo('ln -sf %s/conf/supervisor.conf %s.conf' % (bundle_root,
                                                         bundle_name))

    if 'rq' in env and env.rq:
        changed = True  # Always supervisorctl update
        handle_rq(bundle_name, bundle_root, env)

    if 'celery' in env and env.celery:
        changed = True
        handle_celery(bundle_name, bundle_root, env)

    if changed:
        sudo('supervisorctl update')

    # TODO: don't kill all gunicorn instances
    run('kill -HUP `pgrep gunicorn`')

    # All set, user feedback
    ip = run('curl http://ifconfig.me/')
    dns = run('nslookup %s' % env.http_host)
    if ip in dns:
        proto = 'https' if 'ssl_cert' in env else 'http'
        yay("Visit %s://%s" % (proto, env.http_host))
    else:
        err("Deployment successful but make sure %s points to %s" % (
            env.http_host, ip))


@task()
def destroy():
    """Destroys the current bundle"""
    pass

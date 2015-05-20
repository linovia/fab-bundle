from fabric.api import env, run
from .utils import die, template
from .db import postgres


def manage(command, noinput=True):
    """Runs a management command"""
    noinput = '--noinput' if noinput else ''
    run('%s/env/bin/django-admin.py %s %s --settings=settings' % (
        env.bundle_root, command, noinput))


def database_migration():
    if 'migrations' in env:
        if env.migrations == 'nashvegas':
            bundle_name = env.http_host
            manage('upgradedb -l', noinput=False)  # This creates the migration
                                                   # tables

            installed = postgres(
                'psql %s %s -c "select id from nashvegas_migration limit 1;"' %
                ('%s', bundle_name))
            installed = '0 rows' not in installed
            if installed:
                manage('upgradedb -e', noinput=False)
            else:
                # 1st deploy, force syncdb and seed migrations.
                manage('syncdb')
                manage('upgradedb -s', noinput=False)
        elif env.migrations == 'south':
            manage('syncdb')
            manage('migrate')
        elif env.migrations == 'migrations':
            manage('migrate')
        else:
            die("%s is not supported for migrations." % env.migrations)

    else:
        manage('syncdb')


def collectstatic():
    if env.staticfiles:
        manage('collectstatic')


def setup():
    if 'media_url' not in env:
        env.media_url = '/media/'
    if 'media_root' not in env:
        env.media_root = env.bundle_root + '/public' + env.media_url
    if 'static_url' not in env:
        env.static_url = '/static/'
    if 'static_root' not in env:
        env.static_root = env.bundle_root + '/public' + env.static_url
    if not 'staticfiles' in env:
        env.staticfiles = True
    if not 'cache' in env:
        env.cache = 0  # redis DB
    template('settings.py', '%s/settings.py' % env.bundle_root)
    template('wsgi.py', '%s/wsgi.py' % env.bundle_root)

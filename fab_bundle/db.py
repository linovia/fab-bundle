from fabric.api import env, run


def postgres(cmd):
    db_user = '-U postgres'
    if hasattr(env, 'postgres'):
        if 'admin' in env.postgres:
            db_user = '-U %s' % env.postgres['admin']
        if 'hostname' in env.postgres:
            db_user += ' -h %s' % env.postgres['hostname']
    return run(cmd % db_user)


def choose_postgres_template():
    if 'gis' in env and env.gis is False:
        return 'template0'
    else:
        return 'template_postgis'


def creation(bundle_name):
    if 'postgres' in env:
        installed_dbs = postgres('psql %s -l|grep UTF8')
        installed_dbs = [
            db.split('|')[0].strip()
            for db in installed_dbs.split('\n')]

        db_template = choose_postgres_template()

        if env.databases:
            for database in env.databases.values():
                if database['NAME'] in installed_dbs:
                    continue
                args = [
                    '%s',
                    '-T %s' % db_template,
                    '-E UTF8',
                ]
                if 'USER' in database:
                    args.append(' -O %s' % database['USER'])
                args.append(database['NAME'])
                postgres('createdb ' + ' '.join(args))
        else:
            if bundle_name not in installed_dbs:
                postgres('createdb %s -T %s ''-E UTF8 %s' % (
                    '%s', db_template, bundle_name))

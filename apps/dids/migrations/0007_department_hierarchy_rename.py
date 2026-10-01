# Hand-written (makemigrations could not run in this environment — see
# apps/downloads ModuleNotFoundError, unrelated to this change).
#
# Renames the original global lookup model (EVBV/AR/Billing/...) from
# `Department` to `Team`, freeing up the `Department` name for a new,
# tenant-scoped, top-level grouping of DIDs (Department → DIDs → Teams),
# matching the product's actual hierarchy. Preserves existing data via
# Rename* operations rather than Delete+Create.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
        ('dids', '0006_accessgroup_department_accessgroupentry_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- Step 1: rename the old lookup model Department -> Team -------
        migrations.RenameModel(old_name='Department', new_name='Team'),
        migrations.AlterModelOptions(
            name='team',
            options={'ordering': ['name'], 'verbose_name': 'Team', 'verbose_name_plural': 'Teams'},
        ),
        migrations.AlterModelTable(name='team', table='teams'),

        # --- Step 2: rename the caller work-assignment model and its FK ---
        migrations.RenameModel(
            old_name='UserDIDDepartmentAssignment',
            new_name='UserDIDTeamAssignment',
        ),
        migrations.RenameField(
            model_name='userdidteamassignment',
            old_name='department',
            new_name='team',
        ),
        migrations.AlterModelOptions(
            name='userdidteamassignment',
            options={
                'verbose_name': 'User DID Team Assignment',
                'verbose_name_plural': 'User DID Team Assignments',
            },
        ),
        migrations.AlterModelTable(name='userdidteamassignment', table='user_did_team_assignments'),
        migrations.RemoveIndex(model_name='userdidteamassignment', name='idx_uddassign_user'),
        migrations.RemoveIndex(model_name='userdidteamassignment', name='idx_uddassign_did'),
        migrations.RemoveIndex(model_name='userdidteamassignment', name='idx_uddassign_department'),
        migrations.RemoveConstraint(model_name='userdidteamassignment', name='uq_user_did_department_assignment'),
        migrations.AlterField(
            model_name='userdidteamassignment',
            name='user',
            field=models.ForeignKey(help_text='The caller being assigned to work this DID under this team.', on_delete=django.db.models.deletion.CASCADE, related_name='did_team_assignments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='userdidteamassignment',
            name='did',
            field=models.ForeignKey(help_text='The DID the caller works.', on_delete=django.db.models.deletion.CASCADE, related_name='user_team_assignments', to='dids.did'),
        ),
        migrations.AlterField(
            model_name='userdidteamassignment',
            name='team',
            field=models.ForeignKey(help_text='The team the caller works under for this DID.', on_delete=django.db.models.deletion.PROTECT, related_name='user_did_assignments', to='dids.team'),
        ),
        migrations.AddIndex(model_name='userdidteamassignment', index=models.Index(fields=['user'], name='idx_udtassign_user')),
        migrations.AddIndex(model_name='userdidteamassignment', index=models.Index(fields=['did'], name='idx_udtassign_did')),
        migrations.AddIndex(model_name='userdidteamassignment', index=models.Index(fields=['team'], name='idx_udtassign_team')),
        migrations.AddConstraint(
            model_name='userdidteamassignment',
            constraint=models.UniqueConstraint(fields=('user', 'did', 'team'), name='uq_user_did_team_assignment'),
        ),

        # RenameModel/RenameField/AlterModelTable above rename the table and
        # columns, but Postgres's plain auto-generated indexes from the
        # original migration (0006) were created under names hashed from
        # the OLD table+column identifiers and are never renamed by Django
        # — they're just orphaned, dead weight left pointing at columns
        # that no longer exist under those names. Harmless to migrations
        # (their hash won't collide with anything new we create), but stale
        # cruft with misleading old-table-name prefixes, so drop them.
        migrations.RunSQL(
            sql=(
                'DROP INDEX IF EXISTS "user_did_department_assignments_department_id_340843e3"; '
                'DROP INDEX IF EXISTS "user_did_department_assignments_did_id_7a841fd9"; '
                'DROP INDEX IF EXISTS "user_did_department_assignments_user_id_6449575f";'
            ),
            reverse_sql=(
                'CREATE INDEX "user_did_department_assignments_department_id_340843e3" ON "user_did_team_assignments" ("team_id"); '
                'CREATE INDEX "user_did_department_assignments_did_id_7a841fd9" ON "user_did_team_assignments" ("did_id"); '
                'CREATE INDEX "user_did_department_assignments_user_id_6449575f" ON "user_did_team_assignments" ("user_id");'
            ),
        ),

        # --- Step 3: rename AccessGroupEntry.department -> team -----------
        migrations.RemoveConstraint(model_name='accessgroupentry', name='uq_access_group_entry'),
        migrations.RenameField(
            model_name='accessgroupentry',
            old_name='department',
            new_name='team',
        ),
        migrations.AlterField(
            model_name='accessgroupentry',
            name='team',
            field=models.ForeignKey(blank=True, help_text='Leave blank to grant all teams on the DID(s) named above.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_group_entries', to='dids.team'),
        ),
        migrations.AddConstraint(
            model_name='accessgroupentry',
            constraint=models.UniqueConstraint(fields=('group', 'did', 'team'), name='uq_access_group_entry'),
        ),

        # RenameField (department -> team, above) renames the column and its
        # FK constraint, but leaves the plain auto-generated index Postgres
        # created for the original department_id FK column orphaned under
        # its old, column-derived name. Since that name is hashed purely
        # from (table, column) — not the field name Django currently knows
        # it by — Step 6 below's AddField for the *new* accessgroupentry.
        # department column re-derives that exact same hash and collides
        # with it. Drop the orphaned index explicitly before that happens.
        migrations.RunSQL(
            sql='DROP INDEX IF EXISTS "access_group_entries_department_id_6ff23258";',
            reverse_sql='CREATE INDEX "access_group_entries_department_id_6ff23258" ON "access_group_entries" ("team_id");',
        ),

        # --- Step 4: create the new, tenant-scoped, top-level Department --
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, help_text='Unique identifier (UUID v4).', primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='UTC timestamp of record creation.')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='UTC timestamp of last record update.')),
                ('name', models.CharField(max_length=150)),
                ('code', models.CharField(blank=True, default='', help_text="Optional short display code/number for this department (e.g. '1', 'D-100'), unique within the tenant. Purely a display/reference label — not used for lookups.", max_length=20)),
                ('tenant', models.ForeignKey(help_text='The tenant this department belongs to.', on_delete=django.db.models.deletion.CASCADE, related_name='departments', to='tenants.tenant')),
            ],
            options={
                'verbose_name': 'Department',
                'verbose_name_plural': 'Departments',
                'db_table': 'departments',
                'ordering': ['tenant', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('tenant', 'name'), name='uq_department_tenant_name'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(condition=models.Q(('code__gt', '')), fields=('tenant', 'code'), name='uq_department_tenant_code'),
        ),

        # --- Step 5: DID.department (nullable FK to the new Department) ---
        migrations.AddField(
            model_name='did',
            name='department',
            field=models.ForeignKey(blank=True, help_text='The department this DID is grouped under (optional). Must belong to the same tenant as the DID.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dids', to='dids.department'),
        ),

        # --- Step 6: AccessGroupEntry.department (nullable FK, mutually ---
        # exclusive with did) --------------------------------------------
        migrations.AddField(
            model_name='accessgroupentry',
            name='department',
            field=models.ForeignKey(blank=True, help_text='The department this rule grants (all DIDs under it, including ones added later). Mutually exclusive with did.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_group_entries', to='dids.department'),
        ),
        migrations.AlterField(
            model_name='accessgroupentry',
            name='did',
            field=models.ForeignKey(blank=True, help_text='The single DID this rule grants. Mutually exclusive with department.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_group_entries', to='dids.did'),
        ),
        migrations.AddIndex(
            model_name='accessgroupentry',
            index=models.Index(fields=['department'], name='idx_agentry_department'),
        ),
        migrations.AddConstraint(
            model_name='accessgroupentry',
            constraint=models.UniqueConstraint(fields=('group', 'department', 'team'), name='uq_access_group_entry_department'),
        ),
    ]

# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-08-12 13:46
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('construct', '0016_auto_20170728_1331'),
    ]

    operations = [
        migrations.AlterField(
            model_name='constructmutation',
            name='effects',
            field=models.ManyToManyField(related_name='bar', to='construct.ConstructMutationType'),
        ),
    ]

**Description:**
Add a description of your changes here. 

**Jira: ENT-XXXX**

**Merge checklist:**
- [ ] `./manage.py makemigrations` has been run
    - *Note*: This **must** be run if you modified any models.
      - It may or may not make a migration depending on exactly what you modified, but it should still be run.

*Note: Do not commit on a Friday without checking in with your team and committing time sensitive changes.*

**Post merge:**
- [ ] Ensure that your changes went out to the enterprise-access-stage instance
- [ ] Usher changes out and trigger the pipeline of enterprise-access-prod on [GoCD](https://gocd.tools.edx.org/go/pipelines#!/)
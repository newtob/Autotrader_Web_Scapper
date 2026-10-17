# Spec for Database and ORM

/ship-phase write spec 0002 to `@./docs/specs/0002-Database-and-ORM.md` that will implement a ORM and connect to a database.
The ORM chosen is Peewee. The database is Supabase in PostgreSQL mode.

The goal is to replace the output spreadsheet with data in a database.
The database table needs to hold all the data the spreadsheet holds, with 3 new added columns for:

- date-time first found
- date-time most recently found
- days live, computed between the difference between first and most recently found

Existing columns: [title, price, year, miles, fuel_type, detail_url, extras_total, list all extras one column each..., make, model, model-type, postcode, autotrader ad unique key ]

**Table and Column Naming**: snake case

Output a tabel of columns into the spec and state which must be set, and which can be null.

The ORM is setup to ease data connectivity with the database.
The goal will be for this application to run as a Vercel Function (to be build in a later spec), that will run daily in the cloud.

Local env vars will be saved in `.env`, remote will be in Vercel secret store.

The output excel logic should be deleted.

## Peewee ORM

A failed page write shouldn't kill the run, just initiate a re-try loop, with a maximum number of attempts with a backoff timer.
`ty` will flag dynamic model attributes, these can be ignored.

Write a brief new page in the AgentSoul stating Peewee is a good choice for a simple and modern Python ORM.

## Supabase

Project name: AutoTrader_Scraper
Project ID: dfanjkhgptqnrkdhwxlx
region: eu-west-2

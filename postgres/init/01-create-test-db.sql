SELECT 'CREATE DATABASE mantle_ems_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mantle_ems_test')\gexec

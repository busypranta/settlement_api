BEGIN
  FOR r IN (SELECT table_name FROM user_tables) LOOP
    EXECUTE IMMEDIATE 'GRANT SELECT ON ' || DBMS_ASSERT.ENQUOTE_NAME(r.table_name) || ' TO settlement_api';
  END LOOP;
END;
/
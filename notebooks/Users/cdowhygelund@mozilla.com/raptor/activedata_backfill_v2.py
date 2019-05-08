# Databricks notebook source
import json
import requests
from datetime import  timedelta, datetime
from pyspark.sql.functions import pandas_udf, PandasUDFType
import pyspark.sql.types as t
import pandas as pd

# COMMAND ----------

# MAGIC %md # Variables

# COMMAND ----------

# dates = pd.DataFrame({'date': pd.date_range(start='03/19/2018', end='04/19/2019', freq='H')})
dates = pd.DataFrame({'date': pd.date_range(start='01/01/2019', end='04/19/2019', freq='H')})

# COMMAND ----------

# MAGIC %md # Privates

# COMMAND ----------

url = 'http://activedata.allizom.org/query'
epoch = datetime(1970, 1, 1)
schema = t.StructType([
  t.StructField('build.branch', t.StringType(), True), 
  t.StructField('build.date', t.IntegerType(), True),
  t.StructField('build.platform', t.StringType(), True),
  t.StructField('build.revision', t.StringType(), True),
  t.StructField('build.revision12', t.StringType(), True),
#   t.StructField('build.type', t.StringType(), True),    # can be list or a scalar
  t.StructField('result.framework.name', t.StringType(), True),
  t.StructField('result.lower_is_better', t.BooleanType(), True),
  t.StructField('result.ordering', t.DoubleType(), True),
  t.StructField('result.samples', t.ArrayType(t.DoubleType()), True),  # can be a list or scaler (see clean_result_samples below)
  t.StructField('result.suite', t.StringType(), True),
  t.StructField('result.test', t.StringType(), True),
  t.StructField('result.unit', t.StringType(), True),
  t.StructField('run.browser', t.StringType(), True),
  t.StructField('run.framework.name', t.StringType(), True),
  t.StructField('run.machine.platform', t.StringType(), True),
  t.StructField('run.key', t.StringType(), True),
  t.StructField('run.name', t.StringType(), True),
  t.StructField('run.suite', t.StringType(), True),
  t.StructField('run.timestamp', t.DoubleType(), True),
#   t.StructField('run.type', t.DoubleType(), True),      # can be a list or scalar  
  t.StructField('task.id', t.StringType(), True)
  ]) 

# COMMAND ----------

# MAGIC %md # Process

# COMMAND ----------

# MAGIC %md Create a list of dates to spread across the cluster. Basically datetimes incremented by an hour.
# MAGIC 
# MAGIC ActiveData limits return to 10K records. An hour limits the results to the 1Ks.

# COMMAND ----------

# MAGIC %md **TODO**: Check that no query goes over 10K.

# COMMAND ----------

spark_df = sqlContext.createDataFrame(dates)

# COMMAND ----------

# MAGIC %md Methods to query ActiveData and return data frame.

# COMMAND ----------

fields = schema.fieldNames()

def get_payload(start, end):
  start_ts = int((start - epoch).total_seconds())
  end_ts = int((end - epoch).total_seconds())
  payload = {
      "from": "perf",
      "limit": 10000,
      "select": fields,
      "where": {
          "and": [
              {
                  "eq": {
                      "build.branch": "mozilla-central"
                  }
              },
              {
                  "eq": {
                      "result.framework.name": "raptor"
                  }
               },
              {
                  "gte": {
                      "run.timestamp": start_ts
                  }
              },
            {
                  "lt": {
                      "run.timestamp": end_ts
                  }
              }
          ]
      }
  }
  return(payload)

def clean_result_samples(x):
  if x is None:
    cleaned = x
  elif isinstance(x, list):
    cleaned = [float(y) for y in x]
  else:
    cleaned = [float(x)]
  return cleaned

def query_raptor(timestamp_start, timestamp_end, retries=5):
  errors = 0
  payload = get_payload(timestamp_start, timestamp_end)
  df = pd.DataFrame(columns = fields)
  while(errors < retries):
    r = requests.post(url, data=json.dumps(payload))  
    try:
      res = json.loads(r.text)
      df = pd.DataFrame(res['data'])
    except Exception as e: 
      errors += 1
      continue    
    else:
      break      
  return df

@pandas_udf(schema, PandasUDFType.GROUPED_MAP)
def get_raptor_tests(x):
  timestamp = x['date'][0]
  timestamp_end = timestamp + timedelta(hours=1)
  payload = get_payload(timestamp, timestamp_end)  
  r = requests.post(url, data=json.dumps(payload))  
  df = query_raptor(timestamp, timestamp_end)
#   try:
#     res = json.loads(r.text)
#     df = pd.DataFrame(res['data'])
#   except Exception as e: 
#     df = pd.DataFrame(columns = fields)
  if df.empty:
    df = pd.DataFrame(index=[1], columns = fields)  
  else:
    df['result.samples'] = df['result.samples'].apply(clean_result_samples)
  return df  

# COMMAND ----------

results = spark_df.groupby('date').apply(get_raptor_tests)

# COMMAND ----------

# MAGIC %md Rename columns to friendlier convention and filter out dates with no data (and potential failures).

# COMMAND ----------

col_names = [x.replace('.', '_') for x in fields]
cleaned = results.toDF(*col_names).dropna(how='all')

# COMMAND ----------

cleaned.cache()

# COMMAND ----------

data_bucket = "net-mozaws-prod-us-west-2-pipeline-analysis"
s3path = 'cdowhygelund/raptor/v2/activedata_1yr_v3'
# s3path = 'cdowhygelund/raptor/v2/activedata'
cleaned.write.parquet('s3://{}/{}'.format(data_bucket, s3path), mode='overwrite')

# COMMAND ----------

cleaned.count()
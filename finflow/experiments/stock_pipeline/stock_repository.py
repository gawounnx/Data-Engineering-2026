from __future__ import annotations

import pandas as pd
from google.cloud import bigquery

PROJECT_ID = "melodic-furnace-501500-g4"
DATASET = "data_sample"
TABLE = "stock_prices"


class StockRepository:
    def __init__(self) -> None:
        self.client = bigquery.Client(project=PROJECT_ID)

    def get_stock_names(self) -> list[str]:
        query = f"""
        SELECT DISTINCT itms_nm
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        ORDER BY itms_nm
        """

        df = self.client.query(query).to_dataframe()

        return df["itms_nm"].tolist()

    def get_stock_price(self, stock_name: str) -> pd.DataFrame:
        query = f"""
        SELECT
            PARSE_DATE('%Y%m%d', bas_dt) AS trade_date,
            itms_nm,
            clpr,
            mkp,
            hipr,
            lopr,
            trqu,
            flt_rt
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE itms_nm = @stock_name
        ORDER BY trade_date
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "stock_name",
                    "STRING",
                    stock_name,
                )
            ]
        )

        return self.client.query(
            query,
            job_config=job_config,
        ).to_dataframe()
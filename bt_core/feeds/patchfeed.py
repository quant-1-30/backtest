
import numpy as np
import polars as pl

from bt_core.feed import DataBase


class ParquetPatch(DataBase):    
    lines = ('datetime',) 
    params = (
        ("parquet_path", None),
    )
    
    def _start(self, *args, **kwargs):
        super()._start(*args, **kwargs)

        lf = pl.scan_parquet(self.p.parquet_path)
        df = lf.sort("tick", descending=False).collect()

        arrays = [df[col].to_numpy() for col in self.p.cols]
        self._row_iter = zip(*arrays)


    def _start(self, *args, **kwargs):
        super()._start(*args, **kwargs)

        self.global_scores = {}
        
        if self.p.parquet_path and os.path.exists(self.p.parquet_path):
            # LazyFrame
            lazy_df = (
                pl.scan_parquet(self.p.parquet_path)
                .filter(pl.col("score") > 0.0)
                .with_columns(
                    (pl.col("day").dt.year() * 10000 + 
                     pl.col("day").dt.month() * 100 + 
                     pl.col("day").dt.day()).alias("day_id")
                )
                .group_by("day_id")
                .agg([
                    pl.col("sid"), 
                    pl.col("fsm_score")
                ])
            )
            
            res_dict = lazy_df.collect().to_dict(as_series=False) # avoid iter_rows 
            
            if "day_id" in res_dict and len(res_dict["day_id"]) > 0:
                self.global_scores = {
                    day_id: {s.encode(): float(score) for s, score in zip(sids, scores)}
                    for day_id, sids, scores in zip(res_dict["day_id"], res_dict["sid"], res_dict["fsm_score"])
                }
                
            print(f"[bt_core] {len(self.global_scores)} trading_days signal")
        else:
            print(f"[bt_core] no found: {self.p.parquet_path}")

    def _load(self):
        return False

    def get_topk(self, current_day: int) -> dict:
        return self.global_scores.get(current_day, {})

    def stop(self):
        super().stop()
        self.global_scores.clear()



class SignalPatch(DataBase):
    lines = ('datetime',) 
    params = (
        ("sid", b""),
    )

    def _start(self, *args, **kwargs):
        self.sid = self.p.sid
    
    def _load(self):
        return False

    def get_topk(self, current_day: int) -> dict:
        return {self.sid: 0.0}

    def stop(self):
        super().stop()
        self.global_scores.clear() 
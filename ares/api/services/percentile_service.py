
import pandas as pd
from django.conf import settings
from scipy.stats import percentileofscore
import glob
import os

class PercentileService:
    """
    A service to calculate the percentile of voice analysis scores against a dataset.
    """
    _instance = None
    _data = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PercentileService, cls).__new__(cls)
            cls._instance.load_data()
        return cls._instance

    def load_data(self):
        """
        Loads the interview score data from CSV files in the ares/data directory.
        This method is designed to run once and cache the data.
        """
        if self._data is not None:
            return

        data_path = os.path.join(settings.BASE_DIR, 'data')
        csv_files = glob.glob(os.path.join(data_path, '*_normalized.csv'))
        
        if not csv_files:
            # In a real scenario, you might want to log this error.
            print("Warning: No normalized CSV files found in ares/data.")
            self._data = pd.DataFrame()
            return

        df_list = [pd.read_csv(file) for file in csv_files]
        self._data = pd.concat(df_list, ignore_index=True)
        print(f"Successfully loaded {len(self._data)} records for percentile analysis.")


    def get_percentiles(self, scores, filters=None):
        """
        Calculates the percentile, mean, and std for a given set of scores against the loaded data,
        applying any specified filters.

        :param scores: A dict of the user's scores (e.g., {'confidence_score': 85}).
        :param filters: A dict of filters to apply (e.g., {'gender': 'MALE'}).
        :return: A dict containing the analysis for each score.
        """
        if self._data.empty:
            return {key: {'percentile': 0, 'mean': 0, 'std': 0, 'user_score': scores.get(key, 0)} for key in scores}

        filtered_df = self._data.copy()

        if filters:
            for key, value in filters.items():
                if key in filtered_df.columns and value:
                    if isinstance(value, list):
                        filtered_df = filtered_df[filtered_df[key].isin(value)]
                    else:
                        filtered_df = filtered_df[filtered_df[key] == value]

        results = {}
        for score_name, user_score in scores.items():
            if score_name in filtered_df.columns and not filtered_df.empty:
                score_data = filtered_df[score_name].dropna()
                if not score_data.empty:
                    percentile = percentileofscore(score_data, user_score, kind='weak')
                    mean = score_data.mean()
                    std = score_data.std()

                    results[score_name] = {
                        'percentile': round(percentile, 2),
                        'mean': round(mean, 2),
                        'std': round(std, 2),
                        'user_score': user_score
                    }
                else:
                    results[score_name] = {'percentile': 0, 'mean': 0, 'std': 0, 'user_score': user_score}
            else:
                results[score_name] = {'percentile': 0, 'mean': 0, 'std': 0, 'user_score': user_score}

        return results

# Instantiate the service so it loads the data on server startup
percentile_service = PercentileService()

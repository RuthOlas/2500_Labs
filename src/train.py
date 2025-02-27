import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import category_encoders as ce
import numpy as np
import pickle
import os
from mlflow.models.signature import infer_signature
import yaml
import mlflow

class ModelTrainer:
    def __init__(self, config_path):
        self.config_path = config_path
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        mlflow.sklearn.autolog()

    def combine_dataframe(self, df1, df2):
        combined_dataframe = pd.concat([df1, df2], axis=0)
        combined_dataframe.reset_index(drop=True, inplace=True)
        return combined_dataframe

    def create_lags_no_group(self, df, feature, n_lags):
        for i in range(1, n_lags + 1):
            df[f'{feature}_lag{i}'] = df[feature].shift(i)
        return df

    def train_model(self, data, start_year, n_lags, target, params):
        pollutants = [target]
        additional_features = [
            'Population', 'Number_of_Employees', 'Release_to_Air(Fugitive)', 'Release_to_Air(Other_Non-Point)',
            'Release_to_Air(Road dust)', 'Release_to_Air(Spills)', 'Release_to_Air(Stack/Point)',
            'Release_to_Air(Storage/Handling)', 'Releases_to_Land(Leaks)', 'Releases_to_Land(Other)',
            'Releases_to_Land(Spills)', 'Sum_of_release_to_all_media_(<1tonne)'
        ]
        
        for feature in pollutants + additional_features:
            data = self.create_lags_no_group(data, feature, n_lags)
        
        data = data.dropna()
        province_encoded = pd.get_dummies(data['PROVINCE'], prefix='PROVINCE', drop_first=True, dtype=int)
        data = pd.concat([data, province_encoded], axis=1)
        estimation_encoded = pd.get_dummies(data['Estimation_Method/Méthode_destimation'], prefix='Estimation_Method', drop_first=True, dtype=int)
        data = pd.concat([data, estimation_encoded], axis=1)
        
        encoder = ce.TargetEncoder(cols=['City', 'Facility_Name/Installation', 'NAICS Title/Titre_Code_SCIAN', 'NAICS/Code_SCIAN', "Company_Name/Dénomination_sociale_de_l'entreprise"])
        data = encoder.fit_transform(data, data[target])
        
        features = [f'{pollutant}_lag{i}' for pollutant in pollutants for i in range(1, n_lags + 1)] + \
                   [f'{feature}_lag{i}' for feature in additional_features for i in range(1, n_lags + 1)] + \
                   list(province_encoded.columns) + ['City', 'Facility_Name/Installation', 'NAICS Title/Titre_Code_SCIAN', 'NAICS/Code_SCIAN', "Company_Name/Dénomination_sociale_de_l'entreprise"] + \
                   list(estimation_encoded.columns)

        if 'Region' in data.columns:
            features.append('Region')

        train_data = data[data['Reporting_Year/Année'] < start_year]
        test_data = data[data['Reporting_Year/Année'] >= start_year]

        X_train = train_data[features]
        y_train = train_data[target]
        X_test = test_data[features]
        y_test = test_data[target]

        pipeline = Pipeline([('scaler', StandardScaler()), ('regressor', RandomForestRegressor(random_state=42, **(params if params else {})))])
        pipeline.fit(X_train, y_train)

        # Evaluate model performance on the test set
        y_pred = pipeline.predict(X_test)
        metrics = {
            'Root Mean Squared Error': np.sqrt(mean_squared_error(y_test, y_pred)),
            'Mean Absolute Error': mean_absolute_error(y_test, y_pred),
            'R² Score': r2_score(y_test, y_pred)
        }

        # Specify the directory and file name where the model should be saved
        model_directory = self.config['model_directory']
        model_filename = 'random_forest_model.pkl'

        # Ensure the directory exists
        os.makedirs(model_directory, exist_ok=True)

        # Save the trained model using pickle
        model_path = os.path.join(model_directory, model_filename)
        with open(model_path, 'wb') as f:
            pickle.dump(pipeline, f)

        print(f"Model saved to {model_path}")

        with mlflow.start_run() as run:
            mlflow.log_params(params)

            pipeline.fit(X_train, y_train)

            # Evaluate model performance on the test set
            y_pred = pipeline.predict(X_test)
            metrics = {
                'Root Mean Squared Error': np.sqrt(mean_squared_error(y_test, y_pred)),
                'Mean Absolute Error': mean_absolute_error(y_test, y_pred),
                'R² Score': r2_score(y_test, y_pred)
            }

            # Log metrics in MLflow
            for metric, value in metrics.items():
                mlflow.log_metric(metric, value)

            # Log the trained model with input example and signature
            input_example = X_train.head(1)
            signature = infer_signature(X_train, y_train)
            mlflow.sklearn.log_model(pipeline, artifact_path="model", input_example=input_example, signature=signature)

            # Save model locally
            with open(model_path, 'wb') as f:
                pickle.dump(pipeline, f)

            print(f"Model saved to {model_path}")
            print(f"Run ID: {run.info.run_id}")

            # Update the configuration with the latest run_id and parameters
            self.config['run_id'] = run.info.run_id
            self.config['start_year'] = start_year
            self.config['n_lags'] = n_lags
            self.config['target'] = target
            self.config['model_params'] = params

            # Save the updated configuration back to the YAML file
            with open(self.config_path, 'w') as f:
                yaml.safe_dump(self.config, f)

        return pipeline, metrics

    def main(self):
        train_path = self.config['train_path']
        test_path = self.config['test_path']
        combined_data_path = self.config['combined_data_path']
        
        # Prompt the user for input parameters
        start_year = input(f"Enter start year for training data (default: {self.config['start_year']}): ") or self.config['start_year']
        n_lags = input(f"Enter number of lags to create (default: {self.config['n_lags']}): ") or self.config['n_lags']
        target = input(f"Enter target variable for prediction (default: {self.config['target']}): ") or self.config['target']
        n_estimators = input(f"Enter number of trees in the forest (default: {self.config['model_params']['n_estimators']}): ") or self.config['model_params']['n_estimators']
        max_depth = input(f"Enter maximum depth of the tree (default: {self.config['model_params']['max_depth']}): ") or self.config['model_params']['max_depth']

        # Convert input parameters to the correct types
        start_year = int(start_year)
        n_lags = int(n_lags)
        n_estimators = int(n_estimators)
        max_depth = int(max_depth)

        params = {
            'n_estimators': n_estimators,
            'max_depth': max_depth
        }

        # Load and preprocess data
        df_train = pd.read_csv(train_path)
        df_test = pd.read_csv(test_path)

        combined_df = self.combine_dataframe(df_train, df_test)
        
        # Save the combined data
        combined_df.to_csv(combined_data_path, index=False)
        print(f"Combined data saved to {combined_data_path}")

        model, metrics = self.train_model(combined_df, start_year, n_lags, target, params)
        print("Model training complete and saved.")
        print("Metrics:\n", metrics)

if __name__ == "__main__":
    trainer = ModelTrainer('/home/rutholasupo/2500_Labs/configs/train_config.yaml')
    trainer.main()
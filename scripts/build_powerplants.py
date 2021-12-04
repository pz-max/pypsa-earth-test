# SPDX-FileCopyrightText: : 2017-2020 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
# coding: utf-8
"""
Retrieves conventional powerplant capacities and locations from `powerplantmatching <https://github.com/FRESNA/powerplantmatching>`_, assigns these to buses and creates a ``.csv`` file. It is possible to amend the powerplant database with custom entries provided in ``data/custom_powerplants.csv``.
Relevant Settings
-----------------
.. code:: yaml
    electricity:
      powerplants_filter:
      custom_powerplants:
.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`electricity`
Inputs
------
- ``networks/base.nc``: confer :ref:`base`.
- ``data/custom_powerplants.csv``: custom powerplants in the same format as `powerplantmatching <https://github.com/FRESNA/powerplantmatching>`_ provides
Outputs
-------
- ``resource/powerplants.csv``: A list of conventional power plants (i.e. neither wind nor solar) with fields for name, fuel type, technology, country, capacity in MW, duration, commissioning year, retrofit year, latitude, longitude, and dam information as documented in the `powerplantmatching README <https://github.com/FRESNA/powerplantmatching/blob/master/README.md>`_; additionally it includes information on the closest substation/bus in ``networks/base.nc``.
    .. image:: ../img/powerplantmatching.png
        :scale: 30 %
    **Source:** `powerplantmatching on GitHub <https://github.com/FRESNA/powerplantmatching>`_
Description
-----------
The configuration options ``electricity: powerplants_filter`` and ``electricity: custom_powerplants`` can be used to control whether data should be retrieved from the original powerplants database or from custom amendmends. These specify `pandas.query <https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.query.html>`_ commands.
1. Adding all powerplants from custom:
    .. code:: yaml
        powerplants_filter: false
        custom_powerplants: true
2. Replacing powerplants in e.g. Germany by custom data:
    .. code:: yaml
        powerplants_filter: Country not in ['Germany']
        custom_powerplants: true
    or
    .. code:: yaml
        powerplants_filter: Country not in ['Germany']
        custom_powerplants: Country in ['Germany']
3. Adding additional built year constraints:
    .. code:: yaml
        powerplants_filter: Country not in ['Germany'] and YearCommissioned <= 2015
        custom_powerplants: YearCommissioned <= 2015
"""
import logging
import os

import numpy as np
import pandas as pd
import geopandas as gpd
import powerplantmatching as pm
import pypsa
import yaml
from _helpers import configure_logging
from scipy.spatial import cKDTree as KDTree
from shapely import wkt

logger = logging.getLogger(__name__)


def add_custom_powerplants(ppl):
    custom_ppl_query = snakemake.input.custom_powerplants
    if not custom_ppl_query:
        return ppl
    add_ppls = pd.read_csv(custom_ppl_query,
                           index_col=0,
                           dtype={"bus": "str"})
    # if isinstance(custom_ppl_query, str):
    # add_ppls.query(custom_ppl_query, inplace=True)

    custom_ppls_coords = gpd.GeoSeries.from_wkt(add_ppls['geometry'])
    add_ppls = (
        add_ppls.rename(
            columns={
                "tags.generator:source": "Fueltype", 
                "tags.generator:type": "Technology",
                "tags.power": "Set",                  
                "power_output_MW": "Capacity"
            }
        )
        .replace(
            dict(
                Fueltype={
                    "nuclear" : "Nuclear",
                    "wind" : "Wind",
                    "hydro" : "Hydro",
                    "tidal" : "Other",
                    "wave" : "Other",
                    "geothermal" : "Geothermal",
                    "solar" : "Solar",
                    # NB It may be "Hard Coal" with just the same success
                    "coal" : "Lignite",
                    "gas" : "Natural Gas",
                    "biomass" : "Bioenergy",
                    "biofuel" : "Bioenergy",
                    "biogas" : "Bioenergy",
                    "oil" : "Oil",
                    "diesel" : "Oil",
                    "gasoline" : "Oil",
                    "waste" : "Waste",
                    "battery" : "Other",
                    "osmotic" : "Other",
                    "wave" : "Other"
                },

                # 'Reservoir' 'Pumped Storage' 'Run-Of-River' 'Steam Turbine' 'CCGT' 'OCGT'
                # nan 'Pv' 'CCGT, Thermal' 'Offshore' 'Storage Technologies'
                Technology={
                    "combined_cycle" : "CCGT",
                    "gas_turbine" : "OCGT",
                    "steam_turbine" : "Steam Turbine",
                    "reciprocating_engine" : "Combustion Engine",
                    # a very strong assumption
                    "horizontal_axis" : "Onshore",
                    "vertical_axis" : "Onshore"

                    # 'solar'
                    # 'thermal'
                    #     'steam_turbine', 'solar_thermal_collector'
                    # 'photovoltaic'
                    #     'solar_photovoltaic_panel'
                    # Pv  

                    # Run-Of-River, Pumped Storage, Reservoir => see "tags.generator:method"

                    # The nuclear PP can be mapped into "Steam Turbine"
            },

                    # map into
                    # Power Plant (PP), Combined Heat and Power (CHP), Storages (Stores) 
                    # 'Store' 'PP' 'CHP'
                    Set={
                        "generator" : "PP",
                        "plant" : "PP"
                        # Storages (Stores)=> see "tags.generator:source" == "battery"
                        # CHP?
                    }

            )
        )
        .drop(columns=["tags.generator:method", 
            "geometry"])
        .assign(Name = "",
            Efficiency = "",
            Duration = "",
            Volume_Mm3 = "", 
            DamHeight_m = "", 
            StorageCapacity_MWh = "", 
            DateIn = "", 
            DateRetrofit = "", 
            DateMothball = "",
            DateOut = "",
            lat = custom_ppls_coords.y, 
            lon = custom_ppls_coords.x,
            EIC = "", 
            projectID = ""
        )    

    )
    return ppl.append(add_ppls,
                      sort=False,
                      ignore_index=True,
                      verify_integrity=True)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        snakemake = mock_snakemake("build_powerplants")

    configure_logging(snakemake)

    with open(snakemake.input.pm_config, "r") as f:
        config = yaml.safe_load(f)

    n = pypsa.Network(snakemake.input.base_network)
    countries = n.buses.country.unique()
    config["target_countries"] = countries

    ppl = (pm.powerplants(
        from_url=False,
        config=config).powerplant.fill_missing_decommyears().query(
            'Fueltype not in ["Solar", "Wind"] and Country in @countries').
           replace({
               "Technology": {
                   "Steam Turbine": "OCGT"
               }
           }).assign(Fueltype=lambda df: (df.Fueltype.where(
               df.Fueltype != "Natural Gas",
               df.Technology.replace("Steam Turbine", "OCGT").fillna("OCGT"),
           ))))

    ppl_query = snakemake.config["electricity"]["powerplants_filter"]
    if isinstance(ppl_query, str):
        ppl.query(ppl_query, inplace=True)

    ppl = add_custom_powerplants(ppl) # add carriers from own powerplant files

    cntries_without_ppl = [
        c for c in countries if c not in ppl.Country.unique()
    ]

    for c in countries:
        substation_i = n.buses.query("substation_lv and country == @c").index
        kdtree = KDTree(n.buses.loc[substation_i, ["x", "y"]].values)
        ppl_i = ppl.query("Country == @c").index

        tree_i = kdtree.query(ppl.loc[ppl_i, ["lon", "lat"]].values)[1]
        ppl.loc[ppl_i, "bus"] = substation_i.append(pd.Index([np.nan]))[tree_i]

    if cntries_without_ppl:
        logging.warning(
            f"No powerplants known in: {', '.join(cntries_without_ppl)}")

    bus_null_b = ppl["bus"].isnull()
    if bus_null_b.any():
        logging.warning(
            f"Couldn't find close bus for {bus_null_b.sum()} powerplants")

    ppl.to_csv(snakemake.output[0])

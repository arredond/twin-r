Mon, Aug 24 at 11:52 AM

Quiero aprender todo sobre el simulador de sismos Merisur, desarrollado por la UPM. Importante:

De dónde salen los datos de fallas, edificaciones, etc
Qué modelos usa de simulación (OpenQuake u otros)
Cómo simula Damage o Damage + Debris

Sí. He estado rastreando específicamente el simulador MERISUR de la UPM, sus publicaciones, el material del proyecto y las bases de datos que cita. Hay bastante información dispersa, pero se puede reconstruir con bastante precisión cómo funciona.

Simulador MERISUR — UPM

La conclusión más importante de entrada es esta:

MERISUR no parece ser un wrapper de OpenQuake. El simulador web utiliza una cadena de cálculo propia: ruptura/falla → GMPE de Akkar et al. (2014) + amplificación local de Lorca → espectro de demanda → IDCM/FEMA 440 → daño por edificio → modelo geométrico de escombros. OpenQuake aparece en trabajos posteriores del entorno UPM/MERISUR y en otros análisis de peligrosidad, pero no encuentro evidencia de que el simulador web MERISUR ejecute OpenQuake.

1. Qué es realmente MERISUR

MERISUR significa “Metodología para la Evaluación Efectiva del Riesgo Sísmico Urbano”, proyecto CGL2013-40492-R de la UPM financiado por el Ministerio de Economía y Competitividad. El proyecto era bastante más amplio que el simulador web: incluía peligrosidad, vulnerabilidad, exposición, daños, pérdidas económicas, exposición temporal de personas/vehículos y mitigación.

El simulador web es una implementación bastante concreta de una parte de esa metodología:

escenario sísmico → movimiento del suelo → daño de edificios → escombros en calles/espacios abiertos.

La propia página actual indica que es la versión 1.2.2, aunque también dice “Last Update 2018”, lo que probablemente refleja una actualización de versión/mantenimiento posterior pero no un desarrollo metodológico reciente.

El trabajo que documenta directamente el simulador es:

Pouye Yazdi, Gaspar-Escribano, Martínez-Cuevas & Chavarria-Meneces (2018), “Web-based tool for scenario-based seismic risk assessment”.

Y el trabajo metodológico principal es:

Gaspar-Escribano et al. (2017), “Methodology for an effective risk assessment of urban areas: progress and first results of the MERISUR project”.

2. La arquitectura completa

Podemos reconstruir MERISUR aproximadamente así:

                ┌────────────────────┐
                │   SOURCE / FAULT   │
                │                    │
                │ QAFI fault DB      │
                │ or user-defined    │
                │ rupture             │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │  EARTHQUAKE MODEL  │
                │                    │
                │ Mw, depth, strike, │
                │ dip, Ztor, rake    │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │       GMPE         │
                │                    │
                │ Akkar et al. 2014 │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │     SITE EFFECT     │
                │                    │
                │ Lorca microzonation│
                │ Navarro et al.     │
                │ 2014               │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ GROUND MOTION /    │
                │ RESPONSE SPECTRUM  │
                └─────────┬──────────┘
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
       ┌────────────────┐   ┌────────────────┐
       │ BUILDING       │   │ BUILDING       │
       │ EXPOSURE       │   │ VULNERABILITY  │
       │                │   │                │
       │ footprint      │   │ typology       │
       │ floors         │   │ vulnerability  │
       │ etc.           │   │ class          │
       └───────┬────────┘   └───────┬────────┘
               └──────────┬─────────┘
                          ▼
                ┌────────────────────┐
                │       IDCM         │
                │ FEMA 440 / ATC     │
                │                    │
                │ capacity + demand  │
                └─────────┬──────────┘
                          ▼
                ┌────────────────────┐
                │ BUILDING DAMAGE    │
                │ None / Slight /    │
                │ Moderate /         │
                │ Extensive /        │
                │ Complete           │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │   DEBRIS MODEL     │
                │                    │
                │ damage + façade    │
                │ geometry → debris  │
                └─────────┬──────────┘
                          ▼
                ┌────────────────────┐
                │ OPEN-SPACE DAMAGE  │
                │                    │
                │ sidewalks / roads  │
                │ debris volume      │
                │ blockage           │
                └────────────────────┘

Esta arquitectura está extraordinariamente bien documentada en el paper de 2018.

3. ¿De dónde salen las fallas?

Aquí tenemos una respuesta bastante clara.

QAFI

El menú Automatic del simulador permite seleccionar una falla existente y generar el terremoto de magnitud máxima asociado a ella. El paper especifica explícitamente:

“Faults database according to the QAFI project.”

Es decir, QAFI — Quaternary Active Faults Database of Iberia, desarrollada por el IGME.

QAFI — Base de Datos de Fallas Cuaternarias de Iberia

QAFI contiene fallas con evidencia geológica de actividad durante el Cuaternario, aproximadamente los últimos 2,6 millones de años. Incluye geometría, actividad, tasas de deslizamiento y, en determinadas fallas, parámetros sísmicos como magnitud máxima y recurrencia.

Pero hay una sutileza importante

El QAFI actual no es necesariamente idéntico al QAFI que utilizó MERISUR.

El simulador se desarrolló alrededor de 2017–2018. En aquella época se utilizaba QAFI v3. En QAFI v3, la magnitud máxima se calculaba sistemáticamente a partir de la longitud de la falla mediante relaciones empíricas, salvo cuando había datos paleosismológicos más concluyentes.

En QAFI v4 ese procedimiento fue abandonado: ahora los valores de magnitud máxima/recurrencia solo se muestran cuando existe una estimación publicada.

Por tanto, no asumiría que el “Mmax” que ves actualmente en MERISUR procede de QAFI v4. Es muy probablemente una copia de la base que emplearon durante MERISUR.

4. ¿Qué pasa si no eliges una falla?

El simulador también tiene modo Manual.

Puedes dibujar/definir una ruptura y proporcionar:

latitud/longitud;
magnitud;
strike;
dip;
profundidad superior de la ruptura (Ztor);
mecanismo/focal mechanism.

Esto está documentado tanto en el paper como en la propia interfaz.

Esto es importante porque MERISUR no necesita una falla física de QAFI para hacer el cálculo.

Puedes crear un escenario sintético:

Mw 5.6
strike = 42°
dip = 58°
Ztor = 5 km
rake = ...
posición = ...

y el programa calcula el campo de movimiento y posteriormente el daño.

5. ¿Qué modelo usa para generar el movimiento del suelo?

Aquí tenemos una respuesta especialmente sólida.

Akkar, Sandıkkaya & Bommer (2014)

El simulador utiliza la GMPE de Akkar et al. (2014):

Akkar, S., Sandıkkaya, M.A. & Bommer, J.J. (2014), Empirical ground-motion models for point- and extended-source crustal earthquake scenarios in Europe and the Middle East.

El paper de MERISUR lo identifica explícitamente como:

“The attenuation model: Akkar et al. (2014)”.

La referencia es importante porque no es una simple fórmula de PGA. Es un modelo de movimiento fuerte para terremotos corticales europeos y de Oriente Medio, y puede trabajar con fuentes puntuales y extendidas.

Por tanto:

MERISUR → Akkar 2014 → ground motion

y no:

MERISUR → OpenQuake → GMPE.

6. ¿Y el efecto del suelo?

Aquí entra otro dataset fundamental.

El simulador utiliza una clasificación del suelo de Lorca en cinco categorías, basada en el trabajo de:

Navarro et al. (2014), “Local site effect microzonation of Lorca town (SE Spain)”.

Ese trabajo construyó la microzonificación a partir de mediciones geofísicas, incluyendo información de velocidades de onda S y características de los suelos.

En otras palabras:

Akkar GMPE
     ↓
movimiento de referencia
     ↓
clasificación/microzonificación de Lorca
     ↓
amplificación local
     ↓
movimiento específico en cada edificio

El propio trabajo de MERISUR dice que el soil classification in Lorca is composed of 5 categories.

Esto explica algo importante del simulador: dos edificios muy próximos pueden recibir movimientos diferentes, aunque estén a distancias prácticamente iguales de la ruptura, porque están situados sobre diferentes condiciones de suelo.

7. Entonces, ¿usa OpenQuake?
Para el simulador: aparentemente no.

He buscado específicamente la relación MERISUR–OpenQuake y no encuentro evidencia de que el simulador web utilice OpenQuake como motor.

De hecho, la documentación del simulador identifica directamente:

QAFI → fuente;
Akkar et al. 2014 → atenuación;
Navarro et al. 2014 → suelo;
IDCM/FEMA 440 → daño.

Y el paper dice que el proyecto fue realizado utilizando software y librerías open-source, pero no identifica OpenQuake como componente.

Esto es bastante diferente de OpenQuake, donde normalmente tendríamos explícitamente:

source model
source rupture
GSIM
GMF
exposure model
vulnerability model
ScenarioDamageRiskCalculator

La documentación moderna de OpenQuake, por ejemplo, describe precisamente ese flujo: ruptura + site model + GSIM → GMF → Scenario Damage/Risk.

Pero hay una conexión conceptual

MERISUR y OpenQuake hacen esencialmente el mismo tipo de cadena física:

hazard → ground motion → vulnerability → damage

pero MERISUR implementa una versión mucho más específica para Lorca y basada en sus propios modelos/datos.

8. ¿De dónde salen las edificaciones?

Aquí la historia es bastante más interesante.

El proyecto no simplemente cogió OpenStreetMap.

El punto de partida principal fue la información catastral de Lorca, complementada con otras fuentes.

El trabajo de 2017 dice explícitamente que había disponibles:

datos cartográficos;
geológicos;
administrativos;
catastrales;
censales;
datos de mercado.

Y el trabajo posterior sobre exposición explica mucho mejor cómo construyeron la base.

Trabajo de UPM sobre LiDAR + imágenes + vulnerabilidad en Lorca

Se utilizaron:

1. Catastro

Para geometría y atributos básicos.

2. PNOA

Imágenes aéreas y LiDAR del Plan Nacional de Ortofotografía Aérea.

3. Trabajo de campo

Se hicieron campañas de campo en distintos barrios de Lorca para caracterizar edificios.

4. Información posterior al terremoto de 2011

Hay una base de datos detallada de edificios utilizada como ground truth, especialmente para vulnerabilidad.

9. ¿Qué atributos tiene cada edificio?

El simulador, en su versión descrita en 2018, necesita al menos un shapefile de edificios que contenga:

geometría/contorno;
número de plantas;
clase de vulnerabilidad.

Eso está explícitamente en la descripción del input del simulador.

Pero la investigación de MERISUR desarrolló una base mucho más rica.

A partir de LiDAR y fotografía aérea podían obtener:

footprint;
superficie;
altura;
número de plantas;
tipo de cubierta;
inclinación de cubierta;
características constructivas;
tipología de edificio.
10. ¿Cómo asignan la vulnerabilidad?

Aquí MERISUR tiene varias líneas de investigación que conviene no mezclar.

A. Clasificación empírica

En campañas de campo estudiaron:

sistema estructural;
elementos no estructurales;
altura;
posición dentro de la manzana;
irregularidades;
diferencias de altura con edificios vecinos;
soft storey;
etc.

A partir de esto estudiaron vulnerabilidad mediante EMS-98 y métodos de índice de vulnerabilidad.

B. Modelos mecánicos

Construyeron modelos de elementos finitos de edificios representativos.

Después hicieron pushover / capacity-demand analysis para obtener curvas de capacidad y posteriormente curvas de fragilidad representativas.

Es decir:

edificio real
     ↓
modelo estructural
     ↓
pushover
     ↓
capacity curve
     ↓
fragility / damage model

Esto es importante porque el daño de MERISUR no es simplemente una tabla “PGA → % daño”.

C. Teledetección + Machine Learning

También desarrollaron una metodología para inferir la tipología/vulnerabilidad a partir de:

LiDAR;
ortofotos;
imágenes satelitales.

Y probaron:

Decision Trees;
SVM;
Logistic Regression;
Bayesian Networks.

En el estudio de Lorca obtuvieron aproximadamente 77–80 % de accuracy/F1, dependiendo del método.

Pero ojo: esto pertenece al desarrollo metodológico de MERISUR y no significa necesariamente que el simulador web esté ejecutando esos algoritmos de ML en tiempo real.

El simulador recibe la vulnerability class ya asignada.

11. El modelo de daño: IDCM

Esta es probablemente la parte más interesante para ti.

El simulador dice:

“expected damage is calculated using an analytical model (IDCM, ATC 2005)”

y la referencia [4] es:

Applied Technology Council (2005), FEMA 440 — Improvement of Nonlinear Static Seismic Analysis Procedures.

Aquí IDCM significa Improved Displacement Coefficient Method.

No es un modelo dinámico completo.

La idea es:

Ground-motion spectrum
          +
Building capacity curve
          ↓
Equivalent SDOF
          ↓
Displacement demand
          ↓
Performance point
          ↓
Damage state

El IDCM modifica la demanda de desplazamiento de un sistema equivalente de un grado de libertad mediante coeficientes que intentan representar el comportamiento no lineal. Es precisamente la familia de métodos de análisis estático no lineal desarrollada alrededor de FEMA 440.

12. Por tanto, ¿MERISUR hace un pushover de cada edificio?

No.

Y esto es importante.

El proyecto hizo modelos mecánicos detallados para desarrollar curvas de capacidad/fragilidad representativas.

Después, el simulador utiliza esas curvas para representar clases de edificios.

Es decir:

                    DESARROLLO
                       │
         ┌─────────────┴─────────────┐
         │ modelos estructurales     │
         │ + pushover                │
         └─────────────┬─────────────┘
                       ↓
             curvas capacidad/
                fragilidad
                       │
                       ▼
                 SIMULADOR
                       │
       ┌───────────────┴───────────────┐
       │                               │
   edificio A                      edificio B
   vulnerability                  vulnerability
      class A                        class C
       │                               │
       └───────────────┬───────────────┘
                       ↓
                    IDCM
                       ↓
                  damage state

Esto hace que el simulador sea computacionalmente ligero.

13. ¿Qué significa “Damage” exactamente?

El simulador trabaja con estados:

None
Slight
Moderate
Extensive
Complete
Not estimated

La propia interfaz actual muestra esas categorías.

Y aquí aparece otra característica interesante: el simulador no selecciona simplemente “el daño medio”.

Tiene diferentes niveles de escenario.

High probability

Utiliza:

mediana del ground motion + damage modal

Low probability / high impact

Utiliza:

mediana + 1σ del ground motion + damage modal

Very low probability / very high impact

Utiliza:

mediana + 1σ del ground motion + percentil 85 del daño

Esto está documentado tanto en la interfaz como en el paper.

Por tanto, cuando cambias el selector de probabilidad, no estás cambiando simplemente la magnitud del terremoto.

Estás cambiando cómo se representa la incertidumbre del movimiento y del daño.

14. ¿Cómo pasa de ground motion a damage?

Conceptualmente:

Paso 1

Para cada edificio se calcula el movimiento sísmico local.

Paso 2

Se dispone de la curva de capacidad/fragilidad correspondiente a su clase.

Paso 3

Se obtiene el punto de desempeño mediante IDCM.

Paso 4

Se traduce el desplazamiento/respuesta a una distribución de estados de daño.

Es decir, el resultado realmente es algo parecido a:

Building 123

P(None)       = 0.05
P(Slight)     = 0.25
P(Moderate)   = 0.40
P(Extensive)  = 0.25
P(Complete)   = 0.05

y posteriormente el simulador puede representar el damage grade modal o, para el escenario más severo, el estado correspondiente al percentil 85.

15. Y ahora viene la parte que hace especial a MERISUR: Debris

El objetivo original del proyecto no era únicamente saber:

“¿Qué edificios se dañan?”

sino:

“¿Dónde va a caer ese daño?”

Esto es especialmente importante en Lorca porque muchas de las víctimas del terremoto de 2011 estuvieron asociadas a la caída de elementos no estructurales sobre la vía pública.

Por eso MERISUR distingue:

Primary damage

Daño en el edificio.

Secondary damage

Consecuencias del material desprendido que cae fuera del edificio:

peatones;
vehículos;
mobiliario urbano;
calles;
aceras;
etc.
16. ¿Cómo calcula los escombros?

La versión del simulador descrita en 2018 utiliza fundamentalmente:

damage state + façade area

para determinar el debris producido por cada edificio.

Después lo distribuye espacialmente desde la fachada hacia la calle.

Los anchos utilizados son:

Damage	Extensión del debris
Slight	1 m
Moderate	2 m
Extensive	3 m
Complete	4 m

Estos valores están explícitamente documentados en el paper del simulador.

Por tanto:

               BUILDING
             ┌───────────┐
             │           │
             │           │
             │           │
             └───────────┘
                  │
                  │ façade
                  │
       ───────────┼──────────── street
             ← debris →

Para Complete:

             BUILDING
          ┌─────────────┐
          │             │
          │             │
          └─────────────┘
             █████████
             █████████
             █████████
             █████████     ← hasta 4 m
──────────────────────────────── street
17. Pero el modelo de debris evolucionó

Esto es importante porque hay dos versiones conceptuales que no conviene confundir.

El modelo inicial del simulador web de 2018 es relativamente sencillo: damage grade + fachada → distribución del debris.

Posteriormente, el equipo publicó:

Gaspar-Escribano et al. (2023), “Extending urban seismic risk assessment to open spaces for the 2011 Lorca earthquake scenario”.

Artículo completo / referencia del modelo de debris de MERISUR

Este trabajo formaliza una Debris Accumulation Model y la extensión del riesgo desde el edificio hacia los espacios abiertos.

La idea es:

                    CONVENTIONAL
                    RISK MODEL
                         │
                         ▼
                 damage degree
                         │
                         ▼
                debris generation
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
   debris inside                  debris outside
     building                      building
                                        │
                                        ▼
                              sidewalks / roads
                                        │
                                        ▼
                            secondary consequences

El estudio de 2023 utiliza además datos reales de intervenciones de emergencia y retirada de escombros de Lorca para calibrar/validar el modelo.

18. ¿Qué tal funciona el modelo?

Esta es una de las cosas que me parecen más interesantes.

Para el escenario del terremoto de Lorca de 2011, las estimaciones del modelo:

reproducen el daño observado con diferencias inferiores aproximadamente a medio grado de daño;
producen volúmenes de escombros del mismo orden de magnitud que los observados.

Eso no significa que el modelo prediga perfectamente cada edificio.

Es un modelo urbano agregado, diseñado para responder preguntas como:

“¿Qué calles tienen mayor probabilidad de quedar afectadas/bloqueadas?”

más que:

“¿Qué cornisa concreta del edificio número 172 va a caer?”

19. ¿Qué datos de edificios utiliza realmente el simulador?

Podemos separar tres niveles:

Nivel 1 — input directo del simulador

El shapefile contiene esencialmente:

geometry
number of stories
vulnerability class

según la publicación de 2018.

Nivel 2 — base de datos de exposición de MERISUR

Incluye más información derivada de:

Catastro;
PNOA;
LiDAR;
ortofotos;
trabajo de campo.
Nivel 3 — modelos de vulnerabilidad

Las clases se calibran utilizando:

Risk-UE / Model Building Types;
EMS-98;
observaciones de Lorca;
modelos mecánicos;
pushover;
información de elementos no estructurales;
modificadores urbanísticos.

En particular, el trabajo de teledetección identificó en Lorca seis Model Building Types de Risk-UE, uno de hormigón armado y cinco de mampostería.

20. Algo muy importante: los elementos no estructurales

MERISUR no trata “daño” únicamente como daño estructural.

El proyecto quería explícitamente incorporar:

parapetos;
ornamentación;
cerramientos;
elementos de fachada;
otros elementos no estructurales.

La motivación es precisamente que un edificio puede tener daño estructural relativamente bajo pero generar mucho debris.

Esto es especialmente importante en Lorca.

De hecho, trabajos posteriores del mismo grupo analizaron específicamente edificios residenciales RC de 4–6 plantas y elementos como:

muros de cerramiento;
parapetos;
chimeneas;
revestimientos de piedra.

21. Lo que NO hace MERISUR

Esto ayuda mucho a entender el simulador.

No es:

❌ Una simulación dinámica de estructuras

No integra las ecuaciones de movimiento de cada edificio durante el terremoto.

❌ Un modelo FEM edificio por edificio

Los modelos FEM/pushover se utilizaron para desarrollar las curvas representativas.

❌ Una simulación de fragmentos

No calcula la trayectoria balística de cada pieza de fachada.

❌ Un modelo CFD/DEM de escombros

No simula físicamente cómo chocan y se acumulan las toneladas de rubble.

❌ OpenQuake

No hay evidencia de que el simulador web use OpenQuake como motor.

Es esencialmente:

modelo de movimiento fuerte + análisis de capacidad simplificado + modelo GIS de debris.

22. ¿Dónde entra entonces OpenQuake en todo esto?

Aquí puede haber surgido la confusión.

En la investigación sísmica de la UPM de ese periodo sí se utilizó el ecosistema OpenQuake para determinados estudios de peligrosidad y posteriormente se han desarrollado trabajos de PSHA con modelos de fuentes, incluyendo fallas QAFI.

Por ejemplo, los modelos probabilistas de Murcia/MERISUR consideran:

fuentes de área;
fuentes de falla;
Gutenberg-Richter;
tasas de deslizamiento;
datos paleosismológicos;
GPS;
desagregación.

Pero eso es la investigación de peligrosidad del proyecto, no necesariamente el motor del simulador web.

De hecho, el simulador necesita algo mucho más sencillo:

              escenario definido
                     ↓
                Akkar GMPE
                     ↓
             ground motion
                     ↓
                  IDCM
                     ↓
                  damage

mientras que un cálculo OpenQuake típico podría ser:

source model
     ↓
rupture generation
     ↓
GSIM
     ↓
GMFs
     ↓
exposure
     ↓
vulnerability
     ↓
damage/loss

OpenQuake es mucho más general.

23. La diferencia conceptual más importante

Yo lo resumiría así:

Componente	MERISUR
Área	Lorca / escenarios urbanos
Fallas	QAFI
Ruptura	definida por falla o usuario
GMPE	Akkar et al. 2014
Suelo	microzonificación Navarro et al. 2014
Edificios	Catastro + PNOA/LiDAR + campo
Tipologías	Risk-UE / clasificación propia
Vulnerabilidad	curvas específicas
Damage	IDCM / FEMA 440
Damage states	None–Slight–Moderate–Extensive–Complete
Debris	damage + fachada + geometría urbana
Espacio exterior	aceras + calles
OpenQuake	no parece ser el motor del simulador
FEM	utilizado para desarrollar modelos, no en cada edificio
Objetivo	escenario rápido y espacialmente explícito
24. Y hay una cosa especialmente interesante para ti

Por tu interés en entender exactamente de dónde sale cada número, creo que el siguiente nivel merece la pena.

Hay tres cajas negras que todavía podemos abrir bastante más:

A. Ground motion

Podemos reconstruir matemáticamente:

Akkar 2014 → distancia a ruptura → PGA/Sa(T) → amplificación de Navarro → espectro por edificio

y llegar a las ecuaciones y parámetros concretos.

B. Damage

Podemos reconstruir:

vulnerability class → capacity curve → IDCM/FEMA 440 → performance point → probabilities de None/Slight/Moderate/Extensive/Complete.

Aquí podemos llegar bastante lejos porque FEMA 440 está perfectamente documentado y podemos identificar qué parte utiliza MERISUR.

C. Debris

Esta es la más interesante:

damage state → cantidad de debris → volumen → anchura de acumulación → intersección con calles/aceras → mapa final.

Y aquí hay que distinguir cuidadosamente el modelo del simulador de 2018 del modelo de debris publicado en 2023, porque no son exactamente la misma formulación.

Mi reconstrucción actual del simulador

Si tuviera que implementar una réplica de MERISUR sin acceso al código original, empezaría con:

# 1. Earthquake
rupture = define_rupture(
    lat, lon, Mw, strike, dip, ztor, rake
)

# 2. Ground motion
for building in buildings:

    R = distance(building, rupture)

    spectrum = akkар_2014(
        Mw=Mw,
        distance=R,
        mechanism=rake,
        ...
    )

    # 3. Site effect
    spectrum *= lorca_site_amplification(
        soil_class=building.soil_class
    )

    # 4. Building vulnerability
    capacity = vulnerability_model[
        building.vulnerability_class
    ]

    # 5. Performance point
    displacement = IDCM(
        spectrum=spectrum,
        capacity=capacity
    )

    # 6. Damage
    damage = damage_state(
        displacement,
        fragility_curves
    )

    # 7. Debris
    debris = debris_model(
        damage=damage,
        facade_area=building.facade_area
    )

    # 8. GIS
    allocate_debris_to_street(
        building,
        debris
    )

Esto ya está bastante cerca de la arquitectura real documentada. Lo que todavía no podemos afirmar sin acceder al código/datasets internos son las ecuaciones exactas utilizadas en cada función, los parámetros concretos de las curvas de vulnerabilidad y la fórmula exacta de volumen de debris de la versión desplegada.

Y ahí es donde creo que merece la pena seguir: podemos intentar reconstruir MERISUR casi línea por línea desde sus papers, tesis, referencias y, si conseguimos localizar los ficheros estáticos del servidor de la UPM, incluso identificar los datasets/JS que alimentan el simulador.

¿Qué tendría que hacer para ampliar Merisur a toda España? Ten en cuenta que no podemos hacer ningún tipo de trabajo de campo o específico para alguna zona.

Sí. Y con esa restricción —cero campañas de campo y cero modelos calibrados específicamente para una ciudad/región— yo no intentaría “hacer MERISUR más grande” literalmente. Haría una versión nacional de MERISUR, conservando su arquitectura pero sustituyendo todos los componentes que son específicos de Lorca por modelos nacionales/europeos.

La buena noticia es que España tiene datos suficientes para hacerlo, y hoy incluso hay mejores fuentes que las que tenía MERISUR en 2017.

La idea general

Yo plantearía el proyecto así:

                         MERISUR ESPAÑA
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
       HAZARD              EXPOSURE           VULNERABILITY
          │                   │                   │
     IGN + QAFI          Catastro + PNOA      Taxonomía GEM/
     + GMPE nacional     + LiDAR + INE        modelos europeos
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                              ▼
                         GROUND MOTION
                              │
                              ▼
                           DAMAGE
                              │
                              ▼
                           DEBRIS
                              │
                              ▼
                    ROADS / OPEN SPACES

Y no haría que todo dependiera de OpenQuake. Lo usaría como motor de hazard/damage si nos facilita muchísimo el trabajo, pero mantendría una capa propia para exposición, debris y visualización.

1. Lo primero: identificar qué partes de MERISUR son "Lorca"

Esta es la clave.

Componente MERISUR original	¿Sirve para toda España?	Qué haría
QAFI	🟢	Mantener/ampliar
Magnitud/falla definida por usuario	🟢	Mantener
Akkar et al. 2014	🟢/🟡	Mantener inicialmente
Microzonificación de Lorca	🔴	Sustituir
Catastro	🟢	Sustituir por datos nacionales
PNOA/LiDAR	🟢	Mantener, ahora mucho mejor
Tipologías de Lorca	🔴	Crear taxonomía nacional
Curvas de vulnerabilidad de Lorca	🔴	Sustituir por modelos europeos/nacionales
IDCM/FEMA 440	🟢	Mantener
Modelo de debris	🟢/🟡	Generalizar
Calles/aceras de Lorca	🔴	Sustituir por red nacional

En otras palabras, el problema realmente difícil no es la peligrosidad.

Es:

¿Cómo asignamos una vulnerabilidad razonable a ~11 millones de edificios españoles sin haber visitado ninguno?

Y eso sí tiene solución.

2. Exposición: aquí España es excepcionalmente buena

Yo empezaría por el Catastro.

El objetivo sería generar para cada edificio algo así:

building_id
geometry
municipality
province
construction_year
floor_area
number_of_floors
use
building_height
building_typology
structural_system
vulnerability_class

No necesariamente podremos obtener todos esos campos directamente del Catastro, pero podemos derivarlos.

Y aquí PNOA-LiDAR es tremendamente útil.

La cobertura LiDAR nacional ya tiene tres generaciones:

2009–2015: ~0,5 puntos/m²
2015–2021: 0,5–2 puntos/m²
2022–2025: 5 puntos/m².

Además, el IGN proporciona directamente un MDS normalizado de edificios, que representa su altura sobre el terreno.

Por tanto podemos hacer:

Catastro
   │
   ├── footprint
   ├── año construcción
   ├── uso
   └── referencias
        +
PNOA LiDAR
   │
   ├── altura
   ├── nº plantas estimado
   └── geometría 3D
        +
PNOA ortofoto
   │
   └── información geométrica adicional

Esto es mucho mejor que la situación que tenía MERISUR en Lorca.

3. No intentaría clasificar cada edificio directamente en "vulnerabilidad"

Haría una cadena de inferencia.

Por ejemplo:

                 DATOS OBSERVABLES
                       │
       ┌───────────────┼────────────────┐
       │               │                │
   año edificio     altura          geometría
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                BUILDING TAXONOMY
                       │
                       ▼
             STRUCTURAL TYPOLOGY
                       │
                       ▼
              VULNERABILITY CLASS
                       │
                       ▼
               FRAGILITY CURVES

Esto permite además cuantificar la incertidumbre.

Por ejemplo, un edificio podría ser:

RC frame              55%
Masonry                35%
Other                  10%

en lugar de obligarnos a decir:

“Es hormigón armado.”

Y después propagar esa incertidumbre hasta el daño.

4. La taxonomía: aquí aprovecharía GEM

En lugar de inventarnos una clasificación española desde cero, utilizaría una taxonomía internacional como GEM Building Taxonomy como backbone.

OpenQuake está diseñado precisamente para trabajar así: cada asset tiene una taxonomy, y ésta puede basarse en GEM, PAGER, EMS-98 u otra clasificación.

Esto es muy importante porque permite que el resultado sea interoperable:

Catastro/PNOA
      ↓
Spanish Building Class
      ↓
GEM taxonomy
      ↓
fragility/vulnerability function

Y no nos quedamos encerrados en una taxonomía propia de MERISUR.

5. ¿Podemos hacer esto sin trabajo de campo?

Sí, pero hay que aceptar una cosa:

No vas a obtener una clasificación perfecta de cada edificio.

Lo que sí puedes obtener es una distribución probabilística razonable de tipologías.

Y, para riesgo sísmico nacional, esto es perfectamente defendible.

De hecho, GEM ya dispone de un European Exposure Model que cubre España y estima unos 10,9 millones de edificios y 47,9 millones de ocupantes, aunque su resolución espacial publicada es del orden de 1 km, por lo que yo lo utilizaría como referencia/benchmark, no como sustituto del Catastro si queremos hacer debris.

6. El gran cambio respecto a MERISUR: el suelo

Aquí no podemos copiar el modelo de Lorca.

MERISUR hacía:

Akkar
  ↓
Lorca soil class
  ↓
amplification

Para España necesitamos:

Akkar / GMPE
       ↓
national site model
       ↓
Vs30 / EC8 soil class / basin effects
       ↓
amplification

Y esto debería ser una capa nacional.

Yo utilizaría, en orden de preferencia:

1. Vs30

Si tenemos una estimación espacial suficientemente buena.

2. Clasificación EC8

Por ejemplo:

A
B
C
D
E
3. Modelo de amplificación regional

Si existe una metodología publicada para España que permita convertir geología/Vs30 en factores de amplificación.

La clave es que no podemos hacer microzonificación propia. Por tanto el modelo tiene que proceder de una fuente nacional o europea.

7. Hazard: aquí incluso simplificaría MERISUR

El IGN tiene el mapa oficial de peligrosidad sísmica de España y los valores de PGA asociados al periodo de retorno de 475 años del Anejo Nacional del Eurocódigo 8.

Pero para un simulador de escenarios, no basta con ese mapa.

Necesitamos poder decir:

"Quiero un terremoto Mw 6.2 aquí, con esta ruptura."

Entonces mantendría la arquitectura de MERISUR:

User-defined rupture
       ↓
GMPE
       ↓
GMF

y añadiría:

QAFI fault
       ↓
automatic rupture
       ↓
GMPE
       ↓
GMF

QAFI sigue siendo muy útil para esto.

8. ¿Mantendría Akkar 2014?

Inicialmente, sí.

Tiene una ventaja enorme: es una GMPE desarrollada específicamente para terremotos corticales en Europa y Oriente Medio.

Pero para una versión seria de MERISUR España, haría posteriormente una abstracción:

                 GMPE interface
                       │
          ┌────────────┼────────────┐
          │            │            │
       Akkar 2014   newer GMPE    regional
                                  models

Así el simulador no queda casado con una única GMPE.

Y podríamos comparar Akkar con otras GMPE europeas compatibles con el contexto tectónico.

9. Damage: aquí sí cambiaría bastante MERISUR

Yo no intentaría reproducir literalmente las curvas de vulnerabilidad de Lorca.

La alternativa sería:

Modelo nacional de fragilidad
GEM taxonomy
      ↓
European fragility models
      ↓
Spanish construction-code modifiers
      ↓
fragility curves

Y aquí hay una ventaja enorme:

España tiene una evolución normativa bastante clara.

Podemos incorporar el efecto de:

pre-1960
1960–1970
1970–1990
1990–2000
2000–2012
post-2012

o mejor aún, los años normativos concretos.

Así:

                    construction year
                           │
                           ▼
                    code generation
                           │
                           ▼
building taxonomy ──► seismic resistance
                           │
                           ▼
                    fragility model

Esto probablemente sería más generalizable que el enfoque original de MERISUR.

10. Y aquí OpenQuake sí se vuelve muy interesante

Aunque no utilizaría OpenQuake necesariamente para toda la aplicación, sí aprovecharía su modelo de datos y sus calculadores.

Porque conceptualmente ya tiene exactamente:

Exposure
+
Hazard / GMF
+
Fragility
=
Damage

OpenQuake permite definir funciones de fragilidad que dan la probabilidad de superar distintos estados de daño para determinados niveles de intensidad.

Eso significa que podríamos convertir nuestro modelo en:

MERISUR Spain Exposure
          +
MERISUR Spain Hazard
          +
GEM/Spain Fragility
          ↓
       OpenQuake
          ↓
    Damage states

Y luego:

Damage
   ↓
MERISUR Debris Engine
   ↓
roads / sidewalks / open spaces

Eso me parece una arquitectura mucho más limpia que intentar mantener el código de daño original de MERISUR.

11. El problema más difícil: debris

Aquí sí hay una diferencia fundamental.

Para daño:

building point + damage state

puede ser suficiente.

Para debris necesitas:

building polygon
+
height
+
facade
+
street geometry

Por eso yo mantendría edificios individuales, no el modelo GEM de 1 km.

Y tenemos datos para ello:

Catastro

Footprint.

LiDAR

Altura.

PNOA

Geometría adicional.

IGN / BTN / redes viarias

Calles.

El propio IGN tiene productos nacionales derivados del LiDAR que permiten obtener alturas de edificios.

Así podemos construir:

             building
          ┌─────────────┐
          │             │
          │             │
          └─────────────┘
          ↑             ↑
       facade         facade
          │             │
          ▼             ▼
       debris        debris
          │             │
          └──────┬──────┘
                 ▼
              STREET
12. Yo haría el modelo de debris deliberadamente sencillo

No intentaría hacer una simulación física.

Algo como:

Debris width =
    f(damage state,
      building height,
      building type)

y:

Debris volume =
    f(damage state,
      façade area,
      building volume)

Después:

building façade
       ↓
buffer/sector
       ↓
intersection with roads
       ↓
debris accumulation

Esto conserva la filosofía de MERISUR.

Y tiene una enorme ventaja:

funciona igual en Lorca, Madrid, Granada, Pamplona o Tenerife.

13. Una mejora que sí haría respecto al MERISUR original

En lugar de:

Extensive → 3 m
Complete → 4 m

utilizaría una distribución.

Por ejemplo:

Complete
P(debris width < 2 m) = ...
P(2–4 m) = ...
P(4–6 m) = ...
P(>6 m) = ...

porque una fachada de:

2 plantas
5 plantas
15 plantas

no debería producir exactamente la misma geometría de escombros.

Podemos hacerlo sin ningún trabajo de campo, siempre que la distribución se base en literatura publicada y datos de terremotos existentes.

14. Validación sin trabajo de campo

Aquí hay una condición que considero fundamental.

Aunque no hagamos ningún trabajo de campo, necesitamos validar el modelo.

Y España tiene algunos terremotos que sirven como test cases:

Lorca 2011

El caso de validación principal.

Murcia / SE Spain

Otros eventos históricos/instrumentales.

Granada / Almería

Diferentes contextos tectónicos y de exposición.

Melilla

Muy interesante porque combina:

alta exposición urbana;
sismicidad;
tipologías diferentes.
Canarias

Y aquí probaríamos un régimen tectónico/geológico completamente distinto.

La validación sería:

                  MODEL
                    │
                    ▼
          simulated damage
                    │
                    │
          ┌─────────┴─────────┐
          │                   │
    observed damage       uncertainty
          │                   │
          └─────────┬─────────┘
                    ▼
               calibration

Pero usando únicamente datos históricos ya existentes, no nuevas campañas.

15. Esto nos permite separar "calibración" de "campo"

Y esta distinción es importante para defender el proyecto.

No necesitamos decir:

"Hemos ido a estudiar edificios en Granada."

Podemos decir:

"El modelo nacional utiliza parámetros derivados de bases de datos existentes y literatura científica, y ha sido validado frente a observaciones históricas independientes."

Eso es muchísimo más escalable.

16. El producto final que yo construiría

No intentaría que MERISUR España fuese simplemente:

“elige una falla → mapa de edificios dañados”.

Lo convertiría en una plataforma de scenario-based urban seismic risk.

Por ejemplo:

Input
Epicentre / fault
Mw
Depth
Strike
Dip
Rake
Scenario probability
Hazard
PGA
PGV
Sa(0.2)
Sa(1.0)
...
Exposure
building
taxonomy
height
floors
construction year
occupancy
value
Damage
None
Slight
Moderate
Extensive
Complete
Debris
m³ debris
m² affected street
road blockage
sidewalk blockage
Outputs
Buildings damaged
Buildings collapsed
People exposed
People potentially affected
Debris volume
Affected roads
Blocked roads
Affected open spaces
17. Y mantendría dos escalas

Esto es especialmente importante.

Escala nacional

Para:

"¿Qué pasa si ocurre un Mw 6.5 en Granada?"

No necesitamos procesar 11 millones de edificios individualmente.

Podemos hacer:

10.9 M buildings
       ↓
H3 / grid
       ↓
aggregated exposure
       ↓
damage

El modelo europeo de GEM demuestra que este enfoque es perfectamente viable: su modelo para Europa contiene unos 10,9 M de edificios españoles y ~47,9 M de ocupantes.

Escala urbana

Cuando el usuario hace zoom:

Spain
 ↓
Andalucía
 ↓
Granada
 ↓
neighbourhood
 ↓
individual buildings

Ahí activamos:

Catastro + LiDAR + debris geometry.

Esto sería muchísimo más potente que intentar cargar 11 millones de polígonos en el navegador.

18. Mi arquitectura técnica sería aproximadamente esta
                 ┌─────────────────────┐
                 │      IGN / QAFI      │
                 │   Seismic Hazard     │
                 └──────────┬──────────┘
                            │
                       OpenQuake
                            │
                            ▼
                    Ground Motion Field
                            │
                            ▼
 ┌─────────────────────────────────────────────────┐
 │                  EXPOSURE DB                    │
 │                                                 │
 │ Catastro + PNOA LiDAR + PNOA + INE + OSM/IGN   │
 │                                                 │
 │ building → taxonomy → height → occupancy       │
 └───────────────────────┬─────────────────────────┘
                         │
                         ▼
                Fragility / Vulnerability
                         │
                         ▼
                    DAMAGE MODEL
                         │
                         ▼
                 ┌───────────────┐
                 │ DAMAGE ENGINE │
                 └───────┬───────┘
                         │
               ┌─────────┴─────────┐
               ▼                   ▼
          national damage       urban damage
                                  │
                                  ▼
                            DEBRIS ENGINE
                                  │
                                  ▼
                          ROAD / OPEN SPACE
                                  │
                                  ▼
                             MERISUR UI
19. ¿Qué partes desarrollaría nosotros?

Yo dividiría el proyecto en 5 módulos.

Módulo 1 — Spain Exposure

Probablemente el más laborioso:

Catastro + LiDAR + PNOA → building database.

Módulo 2 — Spain Hazard

IGN + QAFI + GMPE + site model.

Módulo 3 — Spain Vulnerability

building taxonomy → fragility.

Este es el núcleo científico.

Módulo 4 — Debris

Aquí reaprovecharíamos bastante de MERISUR:

damage → debris → street/open-space impact.

Módulo 5 — Interactive simulator

Aquí sí:

mapa web + escenarios + resultados.

20. Lo que yo NO haría

Hay tres tentaciones que evitaría.

❌ 1. No intentaría crear una curva de vulnerabilidad para cada provincia

Eso viola tu premisa y además sería difícil de justificar científicamente.

❌ 2. No usaría únicamente datos agregados a 1 km

Sirven para damage nacional, pero matan completamente el componente debris.

❌ 3. No intentaría mantener todas las ecuaciones de MERISUR

MERISUR fue diseñado para Lorca.

La idea correcta es mantener su filosofía, no necesariamente sus parámetros.

21. Y creo que hay una oportunidad particularmente buena

Con los datos disponibles en 2026, el proyecto sería considerablemente más viable que cuando se desarrolló MERISUR.

Especialmente por PNOA-LiDAR.

La tercera cobertura está llegando a 5 puntos/m² y está cubriendo todo el país; a agosto de 2026 buena parte de los datos ya están publicados, aunque algunos productos derivados siguen en producción.

Eso significa que podemos hacer algo que MERISUR no podía hacer fácilmente en 2017:

inferir automáticamente características geométricas de prácticamente todos los edificios de España.

Y eso encaja muy bien con el enfoque que ya desarrollaron en MERISUR de LiDAR + imágenes → building typology/vulnerability.

Mi recomendación concreta

Si el objetivo es hacer realmente un MERISUR España, no empezaría programando.

Primero haría un "Spain Seismic Exposure & Vulnerability Model" independiente del simulador:

        ┌───────────────────────────┐
        │ Spain Building Database   │
        │                           │
        │ ~11M buildings            │
        │ footprint                 │
        │ height                    │
        │ floors                    │
        │ construction period       │
        │ use                       │
        │ taxonomy                  │
        │ vulnerability             │
        │ uncertainty               │
        └─────────────┬─────────────┘
                      │
                      ▼
             MERISUR / OpenQuake

Si conseguimos construir bien esa base, el resto es relativamente sencillo.

De hecho, yo diría que el 70% del trabajo científico del proyecto está ahí, no en el simulador web.

Y hay una cuestión que merece que la investiguemos a fondo antes de tomar decisiones: qué dataset nacional de Catastro podemos obtener realmente de forma automatizada y qué atributos podemos combinar con PNOA para inferir tipología estructural. Eso determinaría si podemos llegar a un modelo building-by-building para toda España o si tenemos que hacer una solución híbrida (edificio individual en zonas urbanas + exposición agregada en el resto).

Mi objetivo final no es solo replicar Merisur en toda España, sino desarollar un gemelo digital que, aunque inicialmente sirva para riesgo sísmico, con el tiempo se pueda ampliar también a otros riesgos como inundaciones, incendios o clima. ¿Qué partes del simulador sísmico serían aplicables a otros riesgos? Imagino que cosas como la base de datos de exposición, datos demográficos, la red vial, etc

Exactamente. Si el objetivo es un gemelo digital de riesgos multiamenaza de España, yo evitaría diseñar la arquitectura alrededor de “MERISUR sísmico”. Diseñaría una capa común de exposición + contexto territorial + impacto, y pondría cada amenaza como un motor independiente.

La idea fundamental sería:

                         GEMELO DIGITAL
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
      EXPOSICIÓN          TERRITORIO          SOCIEDAD
          │                   │                   │
    edificios            topografía          población
    carreteras           usos suelo          demografía
    infraestructuras     hidrología          movilidad
    empresas             vegetación          actividad
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                         ┌────┴────┐
                         │ RISK    │
                         │ ENGINES │
                         └────┬────┘
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
          SISMO            INUNDACIÓN        INCENDIO
            │                 │                 │
          DAMAGE            DAMAGE            DAMAGE
            │                 │                 │
            └─────────────────┼─────────────────┘
                              │
                         IMPACT ENGINE
                              │
                ┌─────────────┼─────────────┐
                │             │             │
              daños         personas       servicios
                │             │             │
             pérdidas       evacuación    interrupciones

Y ahí MERISUR aporta muchísimo más de lo que parece.

1. Lo que yo consideraría el "núcleo" reutilizable de MERISUR

Separaría sus componentes en tres categorías.

Componente	Sismo	Inundación	Incendio	Clima
Edificios	🟢	🟢	🟢	🟢
Footprints	🟢	🟢	🟢	🟢
Altura / plantas	🟢	🟢	🟢	🟢
Año construcción	🟢	🟢	🟢	🟢
Uso edificio	🟢	🟢	🟢	🟢
Tipología constructiva	🟢	🟢	🟢	🟢
Población	🟢	🟢	🟢	🟢
Demografía	🟢	🟢	🟢	🟢
Carreteras	🟢	🟢	🟢	🟢
Ferrocarril	🟢	🟢	🟢	🟢
Hospitales	🟢	🟢	🟢	🟢
Escuelas	🟢	🟢	🟢	🟢
Red eléctrica	🟢	🟢	🟢	🟢
Red agua	🟢	🟢	🟢	🟢
Topografía	🟡	🟢	🟢	🟢
Hidrología	🔴	🟢	🟡	🟢
Vegetación	🔴	🟡	🟢	🟢
Geología	🟢	🟡	🟢	🟢
Vulnerabilidad	🟢	🟢	🟢	🟢
Hazard	específico	específico	específico	específico
Damage model	específico	específico	específico	específico
Debris	🟢	🟡	🟢	🟡

La conclusión es muy potente:

La mayor parte de la infraestructura de datos no es "sísmica".

2. La verdadera pieza central: Exposure

Yo haría que el concepto central del gemelo digital fuera Asset, no Building.

Un asset podría ser:

{
  "id": "ES-BLD-123456",
  "type": "building",
  "geometry": "...",
  "height": 12.4,
  "floors": 4,
  "construction_year": 1987,
  "use": "residential",
  "occupants": 11,
  "value": 1450000
}

Pero también:

building
road
bridge
railway
hospital
school
power_substation
power_line
water_treatment
water_pipe
gas_pipeline
mobile_tower
industrial_facility
forest
crop
beach

Entonces cada amenaza simplemente pregunta:

¿Qué assets afecta este hazard y qué impacto produce?

3. El edificio no debería tener una "vulnerabilidad"

Esta es una modificación conceptual importante respecto a MERISUR.

No pondría:

Building
   └── vulnerability = 0.65

Sino:

Building
   ├── seismic vulnerability
   ├── flood vulnerability
   ├── wildfire vulnerability
   ├── heat vulnerability
   ├── wind vulnerability
   └── ...

Porque un mismo edificio puede ser:

              Building
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
     Sismo     Flood      Fire
       │         │          │
    masonry    basement    façade
    pre-code   occupied    material
       │         │          │
     high       high       medium

La exposición es común.

La vulnerabilidad es específica del hazard.

4. Y exactamente lo mismo con Hazard

No queremos que el edificio sepa qué es un terremoto.

Queremos una interfaz genérica:

Hazard
  │
  ├── intensity
  ├── spatial distribution
  ├── temporal distribution
  ├── probability
  └── uncertainty

Un terremoto puede tener:

PGA
PGV
Sa(0.2)
Sa(1.0)

Una inundación:

water_depth
flow_velocity
duration

Un incendio:

fire_intensity
radiant_heat
flame_length
arrival_time

Una ola de calor:

temperature
wet_bulb_temperature
duration

La API común podría ser simplemente:

hazard.intensity(asset, time)
5. Esto permite reutilizar directamente el motor de exposición

Por ejemplo, para un terremoto:

Building
   ↓
PGA
   ↓
Seismic fragility
   ↓
Damage

Para inundación:

Building
   ↓
water depth
   ↓
Flood vulnerability
   ↓
Damage

Para incendio:

Building
   ↓
fire intensity
   ↓
Wildfire vulnerability
   ↓
Damage

El asset database es exactamente el mismo.

6. La población es todavía más importante

Aquí creo que hay una oportunidad enorme.

No modelaría simplemente:

building → population = 10

sino:

Population
    │
    ├── spatial distribution
    ├── age
    ├── vulnerability
    ├── employment
    ├── mobility
    └── temporal profile

Porque la población se mueve.

A las 03:00:

residences █████████
offices    ██
schools    ░

A las 10:00:

residences ███
offices    █████████
schools    ██████

Y eso cambia radicalmente el riesgo.

7. Esto es particularmente importante para inundaciones e incendios

Imagina una inundación a las 08:00.

No basta con saber:

2.000 personas viven en la zona.

Necesitamos saber:

¿Dónde están las 2.000 personas cuando llega el agua?

Y para eso la infraestructura común de MERISUR puede evolucionar hacia un population & mobility model.

             Population
                 │
       ┌─────────┴──────────┐
       ▼                    ▼
  Residential           Workplace
       │                    │
       └─────────┬──────────┘
                 ▼
             Mobility
                 │
                 ▼
             Exposure
                 │
                 ▼
               Hazard
8. La red vial es todavía más transversal

Lo que en MERISUR aparece como:

"¿Qué calles quedan bloqueadas por debris?"

se puede generalizar enormemente.

Sismo
debris → road blockage
Inundación
water depth → road blockage
Incendio
fire → road closure
Nieve / hielo
snow/ice → reduced capacity
Deslizamiento
landslide → road blockage

Por tanto yo no llamaría al módulo:

Debris module

sino:

Network Impact Engine

Y tendría:

                    NETWORK
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      roads          rail          utilities
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                 accessibility
                       │
                       ▼
                 network impact
9. Esto permite una cosa mucho más interesante que "damage"

Puedes pasar de:

¿Cuántos edificios están dañados?

a:

¿Qué servicios deja de prestar el territorio?

Por ejemplo:

Earthquake
     ↓
Hospital damaged
     ↓
Roads blocked
     ↓
Ambulance travel time ↑
     ↓
Hospital accessibility ↓
     ↓
Population affected ↑

Eso es mucho más parecido a un gemelo digital real.

10. Infraestructura crítica debería ser una capa propia

Yo crearía una jerarquía:

ASSET
 │
 ├── Building
 │
 ├── Infrastructure
 │     ├── Road
 │     ├── Rail
 │     ├── Bridge
 │     ├── Port
 │     └── Airport
 │
 ├── Utility
 │     ├── Electricity
 │     ├── Water
 │     ├── Gas
 │     └── Telecom
 │
 └── Facility
       ├── Hospital
       ├── School
       ├── Police
       ├── Fire station
       └── Shelter

Y cada asset tiene:

Exposure
+
Vulnerability
+
Criticality
+
Connectivity
11. Criticality es algo que MERISUR no necesitaba tanto

Pero para un gemelo digital sería fundamental.

Un edificio puede tener:

Damage = 80%

pero:

un almacén vacío → impacto relativamente bajo;
un hospital → impacto enorme.

Por tanto:

Risk ≠ Damage

Risk =
    Hazard
    × Exposure
    × Vulnerability
    × Criticality

Y Criticality debería ser independiente del hazard.

12. Topografía es otra capa transversal

Para sismo:

pendiente;
geología;
Vs30.

Para inundación:

DEM;
flow accumulation;
drainage network.

Para incendios:

pendiente;
orientación;
accesibilidad.

Para deslizamientos:

pendiente;
litología;
curvatura;
humedad.

Para clima:

elevación;
orientación;
exposición.

Por tanto:

DEM + terrain derivatives deberían estar en el núcleo del gemelo digital.

13. Vegetación también

En MERISUR prácticamente no era necesaria.

Pero para incendios se vuelve esencial:

land cover
+
fuel type
+
fuel load
+
moisture
+
terrain
+
wind
     ↓
wildfire hazard

Y después vuelve a conectarse con los mismos:

buildings
roads
population
critical infrastructure
14. Una arquitectura que creo que encaja muy bien

Yo lo dividiría en cuatro capas permanentes:

① Digital Twin / Base territorial

Lo que existe independientemente del riesgo:

Buildings
Terrain
Land cover
Roads
Rail
Utilities
Water
Administrative boundaries
② Exposure

Quién/qué está ahí:

Population
Businesses
Vehicles
Economic value
Critical facilities
③ Hazard engines

Uno por amenaza:

Earthquake
Flood
Wildfire
Extreme heat
Drought
Wind
Landslide
④ Impact engines

También especializados:

Building damage
Infrastructure damage
Human exposure
Economic loss
Network disruption
Debris
Evacuation
Service disruption

Esto es mucho más escalable.

15. Y aquí hay una distinción arquitectónica muy importante

No mezclaría:

Hazard

"¿Qué está ocurriendo?"

con:

Exposure

"¿Qué hay ahí?"

con:

Vulnerability

"¿Qué le pasa a ese asset?"

con:

Impact

"¿Qué consecuencias tiene?"

Por ejemplo:

                    FLOOD
                      │
               water depth = 1.2m
                      │
                      ▼
                   BUILDING
                      │
              flood vulnerability
                      │
                      ▼
               damage = 40%
                      │
                      ▼
              €350k economic loss
                      │
                      ▼
             4 residents displaced

Cada etapa es independiente.

16. Y esto hace posible el verdadero "digital twin"

Un simulador convencional hace:

Scenario → Result

Un gemelo digital debería permitir:

CURRENT STATE
     ↓
Scenario
     ↓
Future state
     ↓
Impact
     ↓
Intervention
     ↓
New future state

Por ejemplo:

¿Qué pasa si reforzamos todos los edificios anteriores a 1970?

Baseline
   ↓
Mw 6.2
   ↓
12,500 damaged

Intervención:

retrofit 20,000 buildings

Nueva simulación:

   ↓
7,300 damaged

O:

¿Qué pasa si construimos esta nueva estación de bomberos?

Y entonces puedes evaluar cómo cambia el tiempo de respuesta ante incendios.

Eso ya no es simplemente MERISUR.

Es un territorial risk digital twin.

17. De hecho, diseñaría la API alrededor de esto

Algo conceptualmente parecido a:

GET /assets
GET /buildings
GET /population
GET /infrastructure
GET /networks

POST /hazards/earthquake
POST /hazards/flood
POST /hazards/wildfire

POST /scenarios

GET /scenarios/{id}/hazard
GET /scenarios/{id}/damage
GET /scenarios/{id}/impact
GET /scenarios/{id}/network

POST /interventions
POST /scenarios/{id}/recalculate

Así una amenaza nueva no obliga a rehacer el modelo territorial.

18. Hay incluso una oportunidad para combinar amenazas

Esto se vuelve realmente interesante con el tiempo.

Por ejemplo:

Extreme rainfall
       ↓
Flood
       ↓
Road blockage
       ↓
Emergency accessibility ↓

o:

Heat wave
       +
Wildfire
       ↓
Electricity demand ↑
       ↓
Grid stress

o:

Earthquake
       ↓
Water network damage
       ↓
Firefighting capacity ↓
       ↓
Post-earthquake fire

Por tanto el modelo debería permitir hazards encadenados, no únicamente amenazas independientes.

19. Si lo estuviera diseñando desde cero, estos serían los módulos "core"
CORE DIGITAL TWIN
│
├── Spatial database
│
├── Asset registry
│
├── Building model
│
├── Infrastructure model
│
├── Population model
│
├── Mobility model
│
├── Terrain model
│
├── Land-cover model
│
├── Network graph
│
├── Exposure engine
│
├── Vulnerability framework
├── Criticality framework
├── Impact engine
├── Uncertainty engine
│
└── Scenario engine

Y encima:

HAZARD PLUGINS
│
├── Earthquake
├── Flood
├── Wildfire
├── Heat
├── Drought
├── Wind
├── Landslide
└── ...
20. Mi prioridad para el proyecto sería distinta de "hacer MERISUR España"

Yo lo plantearía en tres fases.

Fase 1 — Digital Twin base

Construir:

España → edificios + infraestructura + población + terreno + redes.

Sin ningún hazard.

Esto es el activo estratégico del proyecto.

Fase 2 — Earthquake

Aquí reutilizamos MERISUR como primer plugin:

QAFI
+
GMPE
+
seismic vulnerability
+
damage
+
debris
+
network impact
Fase 3 — Flood / wildfire / climate

Añadimos nuevos motores sin tocar la base:

                 SAME DIGITAL TWIN
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
       EARTHQUAKE      FLOOD       WILDFIRE
          │             │             │
       damage        damage        damage
          │             │             │
          └─────────────┼─────────────┘
                        ▼
                 COMMON IMPACT
Y hay una decisión que tomaría ahora, no después

No diseñaría la base de datos pensando en edificios.

La diseñaría pensando en assets geoespaciales + relaciones.

Un edificio sería simplemente uno de los tipos de asset.

Eso te permitirá dentro de 5 años añadir:

incendios forestales,
inundación fluvial/pluvial,
calor extremo,
sequía,
viento,
deslizamientos,
terremotos,
contaminación,

sin tener que reconstruir el sistema.

Y lo que has identificado de MERISUR —edificios, demografía, carreteras, infraestructura, topografía, ocupación, vulnerabilidad, etc.— es precisamente la semilla de esa capa común. La parte verdaderamente específica de MERISUR es el hazard sísmico + modelo de daño sísmico + debris. El resto debería convertirse en infraestructura del gemelo digital.
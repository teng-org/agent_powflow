FROM maven:3.9.9-eclipse-temurin-21 AS builder
WORKDIR /build

COPY services/control-plane/pom.xml /build/pom.xml
RUN mvn -q -DskipTests dependency:go-offline

COPY services/control-plane/src /build/src
RUN mvn -q -DskipTests package \
    && cp /build/target/control-plane-*.jar /build/control-plane.jar

FROM eclipse-temurin:21-jre
WORKDIR /app

COPY --from=builder /build/control-plane.jar /app/control-plane.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/control-plane.jar"]

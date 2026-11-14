FROM mcr.microsoft.com/dotnet/sdk:10.0.404 AS build
WORKDIR /src
COPY global.json Directory.Build.props Atlas.slnx ./
COPY backend/ backend/
RUN dotnet restore --locked-mode
RUN dotnet publish backend/Atlas.Api --no-restore -c Release -o /app

FROM mcr.microsoft.com/dotnet/aspnet:10.0.15
WORKDIR /app
RUN install -d -o app -g app /var/lib/atlas/keys
COPY --from=build /app .
USER app
ENTRYPOINT ["dotnet", "Atlas.Api.dll"]
